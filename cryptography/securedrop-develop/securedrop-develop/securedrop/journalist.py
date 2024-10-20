# -*- coding: utf-8 -*-

import os
from datetime import datetime
import functools

from flask import (Flask, request, render_template, send_file, redirect, flash,
                   url_for, g, abort, session)
from flask_wtf.csrf import CSRFProtect
from flask_assets import Environment
from sqlalchemy.orm.exc import MultipleResultsFound, NoResultFound
from sqlalchemy.exc import IntegrityError

import config
import version
import crypto_util
import store
import template_filters
from db import (db_session, Source, Journalist, Submission, Reply,
                SourceStar, get_one_or_else, WrongPasswordException,
                LoginThrottledException, InvalidPasswordLength)
import worker

app = Flask(__name__, template_folder=config.JOURNALIST_TEMPLATES_DIR)
app.config.from_object(config.JournalistInterfaceFlaskConfig)
CSRFProtect(app)

assets = Environment(app)

app.jinja_env.globals['version'] = version.__version__
if getattr(config, 'CUSTOM_HEADER_IMAGE', None):
    app.jinja_env.globals['header_image'] = config.CUSTOM_HEADER_IMAGE
    app.jinja_env.globals['use_custom_header_image'] = True
else:
    app.jinja_env.globals['header_image'] = 'logo.png'
    app.jinja_env.globals['use_custom_header_image'] = False

app.jinja_env.filters['datetimeformat'] = template_filters.datetimeformat


@app.teardown_appcontext
def shutdown_session(exception=None):
    """Automatically remove database sessions at the end of the request, or
    when the application shuts down"""
    db_session.remove()


def get_source(sid):
    """Return a Source object, representing the database row, for the source
    with id `sid`"""
    source = None
    query = Source.query.filter(Source.filesystem_id == sid)
    source = get_one_or_else(query, app.logger, abort)

    return source


@app.before_request
def setup_g():
    """Store commonly used values in Flask's special g object"""
    uid = session.get('uid', None)
    if uid:
        g.user = Journalist.query.get(uid)

    if request.method == 'POST':
        sid = request.form.get('sid')
        if sid:
            g.sid = sid
            g.source = get_source(sid)


def logged_in():
    # When a user is logged in, we push their user id (database primary key)
    # into the session. setup_g checks for this value, and if it finds it,
    # stores a reference to the user's Journalist object in g.
    #
    # This check is good for the edge case where a user is deleted but still
    # has an active session - we will not authenticate a user if they are not
    # in the database.
    return bool(g.get('user', None))


def login_required(func):
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        if not logged_in():
            return redirect(url_for('login'))
        return func(*args, **kwargs)
    return wrapper


def admin_required(func):
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        if logged_in() and g.user.is_admin:
            return func(*args, **kwargs)
        # TODO: sometimes this gets flashed 2x (Chrome only?)
        flash("You must be an administrator to access that page",
              "notification")
        return redirect(url_for('index'))
    return wrapper


@app.route('/login', methods=('GET', 'POST'))
def login():
    if request.method == 'POST':
        try:
            user = Journalist.login(request.form['username'],
                                    request.form['password'],
                                    request.form['token'])
        except Exception as e:
            app.logger.error("Login for '{}' failed: {}".format(
                request.form['username'], e))
            login_flashed_msg = "Login failed."

            if isinstance(e, LoginThrottledException):
                login_flashed_msg += (
                    " Please wait at least {} seconds "
                    "before logging in again.".format(
                        Journalist._LOGIN_ATTEMPT_PERIOD))
            else:
                try:
                    user = Journalist.query.filter_by(
                        username=request.form['username']).one()
                    if user.is_totp:
                        login_flashed_msg += " Please wait for a new two-factor token before logging in again."
                except:
                    pass

            flash(login_flashed_msg, "error")
        else:
            app.logger.info("Successful login for '{}' with token {}".format(
                request.form['username'], request.form['token']))

            # Update access metadata
            user.last_access = datetime.utcnow()
            db_session.add(user)
            db_session.commit()

            session['uid'] = user.id
            return redirect(url_for('index'))

    return render_template("login.html")


@app.route('/logout')
def logout():
    session.pop('uid', None)
    return redirect(url_for('index'))


@app.route('/admin', methods=('GET', 'POST'))
@admin_required
def admin_index():
    users = Journalist.query.all()
    return render_template("admin.html", users=users)


@app.route('/admin/add', methods=('GET', 'POST'))
@admin_required
def admin_add_user():
    if request.method == 'POST':
        form_valid = True

        username = request.form['username']
        if len(username) == 0:
            form_valid = False
            flash("Missing username", "error")

        password = request.form['password']
        password_again = request.form['password_again']
        if password != password_again:
            form_valid = False
            flash("Passwords didn't match", "error")

        is_admin = bool(request.form.get('is_admin'))

        if form_valid:
            try:
                otp_secret = None
                if request.form.get('is_hotp', False):
                    otp_secret = request.form.get('otp_secret', '')
                new_user = Journalist(username=username,
                                      password=password,
                                      is_admin=is_admin,
                                      otp_secret=otp_secret)
                db_session.add(new_user)
                db_session.commit()
            except InvalidPasswordLength:
                form_valid = False
                flash("Your password must be between {} and {} characters.".format(
                        Journalist.MIN_PASSWORD_LEN, Journalist.MAX_PASSWORD_LEN
                    ), "error")
            except IntegrityError as e:
                db_session.rollback()
                form_valid = False
                if "UNIQUE constraint failed: journalists.username" in str(e):
                    flash("That username is already in use",
                          "error")
                else:
                    flash("An error occurred saving this user to the database."
                          " Please check the application logs.",
                          "error")
                    app.logger.error("Adding user '{}' failed: {}".format(
                        username, e))

        if form_valid:
            return redirect(url_for('admin_new_user_two_factor',
                                    uid=new_user.id))

    return render_template("admin_add_user.html")


@app.route('/admin/2fa', methods=('GET', 'POST'))
@admin_required
def admin_new_user_two_factor():
    user = Journalist.query.get(request.args['uid'])

    if request.method == 'POST':
        token = request.form['token']
        if user.verify_token(token):
            flash(
                "Two-factor token successfully verified for user {}!".format(
                    user.username),
                "notification")
            return redirect(url_for("admin_index"))
        else:
            flash("Two-factor token failed to verify", "error")

    return render_template("admin_new_user_two_factor.html", user=user)


@app.route('/admin/reset-2fa-totp', methods=['POST'])
@admin_required
def admin_reset_two_factor_totp():
    uid = request.form['uid']
    user = Journalist.query.get(uid)
    user.is_totp = True
    user.regenerate_totp_shared_secret()
    db_session.commit()
    return redirect(url_for('admin_new_user_two_factor', uid=uid))


@app.route('/admin/reset-2fa-hotp', methods=['POST'])
@admin_required
def admin_reset_two_factor_hotp():
    uid = request.form['uid']
    otp_secret = request.form.get('otp_secret', None)
    if otp_secret:
        user = Journalist.query.get(uid)
        try:
            user.set_hotp_secret(otp_secret)
        except TypeError as e:
            if "Non-hexadecimal digit found" in str(e):
                flash("Invalid secret format: "
                      "please only submit letters A-F and numbers 0-9.",
                      "error")
            elif "Odd-length string" in str(e):
                flash("Invalid secret format: "
                      "odd-length secret. Did you mistype the secret?",
                      "error")
            else:
                flash("An unexpected error occurred! "
                      "Please check the application "
                      "logs or inform your adminstrator.", "error")
                app.logger.error(
                    "set_hotp_secret '{}' (id {}) failed: {}".format(
                        otp_secret, uid, e))
            return render_template('admin_edit_hotp_secret.html', uid=uid)
        else:
            db_session.commit()
            return redirect(url_for('admin_new_user_two_factor', uid=uid))
    else:
        return render_template('admin_edit_hotp_secret.html', uid=uid)


class PasswordMismatchError(Exception):
    pass


def edit_account_password(user, password, password_again):
    if password:
        if password != password_again:
            flash("Passwords didn't match!", "error")
            raise PasswordMismatchError
        try:
            user.set_password(password)
        except InvalidPasswordLength:
            flash("Your password must be between {} and {} characters.".format(
                    Journalist.MIN_PASSWORD_LEN, Journalist.MAX_PASSWORD_LEN
                ), "error")
            raise


def commit_account_changes(user):
    if db_session.is_modified(user):
        try:
            db_session.add(user)
            db_session.commit()
        except Exception as e:
            flash("An unexpected error occurred! Please check the application "
                  "logs or inform your adminstrator.", "error")
            app.logger.error("Account changes for '{}' failed: {}".format(user,
                                                                          e))
            db_session.rollback()
        else:
            flash("Account successfully updated!", "success")


@app.route('/admin/edit/<int:user_id>', methods=('GET', 'POST'))
@admin_required
def admin_edit_user(user_id):
    user = Journalist.query.get(user_id)

    if request.method == 'POST':
        if request.form['username']:
            new_username = request.form['username']
            if new_username == user.username:
                pass
            elif Journalist.query.filter_by(
                username=new_username).one_or_none():
                flash('Username "{}" is already taken!'.format(new_username),
                      "error")
                return redirect(url_for("admin_edit_user", user_id=user_id))
            else:
                user.username = new_username

        try:
            edit_account_password(user, request.form['password'],
                                  request.form['password_again'])
        except (PasswordMismatchError, InvalidPasswordLength):
            return redirect(url_for("admin_edit_user", user_id=user_id))

        user.is_admin = bool(request.form.get('is_admin'))

        commit_account_changes(user)

    return render_template("edit_account.html", user=user)


@app.route('/admin/delete/<int:user_id>', methods=('POST',))
@admin_required
def admin_delete_user(user_id):
    user = Journalist.query.get(user_id)
    if user:
        db_session.delete(user)
        db_session.commit()
        flash("Deleted user '{}'".format(user.username), "notification")
    else:
        app.logger.error(
            "Admin {} tried to delete nonexistent user with pk={}".format(
            g.user.username, user_id))
        abort(404)

    return redirect(url_for('admin_index'))


@app.route('/account', methods=('GET', 'POST'))
@login_required
def edit_account():
    if request.method == 'POST':
        try:
            edit_account_password(g.user, request.form['password'],
                                  request.form['password_again'])
        except (PasswordMismatchError, InvalidPasswordLength):
            return redirect(url_for('edit_account'))

        commit_account_changes(g.user)

    return render_template('edit_account.html')


@app.route('/account/2fa', methods=('GET', 'POST'))
@login_required
def account_new_two_factor():
    if request.method == 'POST':
        token = request.form['token']
        if g.user.verify_token(token):
            flash("Two-factor token successfully verified!", "notification")
            return redirect(url_for('edit_account'))
        else:
            flash("Two-factor token failed to verify", "error")

    return render_template('account_new_two_factor.html', user=g.user)


@app.route('/account/reset-2fa-totp', methods=['POST'])
@login_required
def account_reset_two_factor_totp():
    g.user.is_totp = True
    g.user.regenerate_totp_shared_secret()
    db_session.commit()
    return redirect(url_for('account_new_two_factor'))


@app.route('/account/reset-2fa-hotp', methods=['POST'])
@login_required
def account_reset_two_factor_hotp():
    otp_secret = request.form.get('otp_secret', None)
    if otp_secret:
        g.user.set_hotp_secret(otp_secret)
        db_session.commit()
        return redirect(url_for('account_new_two_factor'))
    else:
        return render_template('account_edit_hotp_secret.html')


def make_star_true(sid):
    source = get_source(sid)
    if source.star:
        source.star.starred = True
    else:
        source_star = SourceStar(source)
        db_session.add(source_star)


def make_star_false(sid):
    source = get_source(sid)
    if not source.star:
        source_star = SourceStar(source)
        db_session.add(source_star)
        db_session.commit()
    source.star.starred = False


@app.route('/col/add_star/<sid>', methods=('POST',))
@login_required
def add_star(sid):
    make_star_true(sid)
    db_session.commit()
    return redirect(url_for('index'))


@app.route("/col/remove_star/<sid>", methods=('POST',))
@login_required
def remove_star(sid):
    make_star_false(sid)
    db_session.commit()
    return redirect(url_for('index'))


@app.route('/')
@login_required
def index():
    unstarred = []
    starred = []

    # Long SQLAlchemy statements look best when formatted according to
    # the Pocoo style guide, IMHO:
    # http://www.pocoo.org/internal/styleguide/
    sources = Source.query.filter_by(pending=False) \
                          .order_by(Source.last_updated.desc()) \
                          .all()
    for source in sources:
        star = SourceStar.query.filter_by(source_id=source.id).first()
        if star and star.starred:
            starred.append(source)
        else:
            unstarred.append(source)
        source.num_unread = len(
            Submission.query.filter_by(source_id=source.id,
                                       downloaded=False).all())

    return render_template('index.html', unstarred=unstarred, starred=starred)


@app.route('/col/<sid>')
@login_required
def col(sid):
    source = get_source(sid)
    source.has_key = crypto_util.getkey(sid)
    return render_template("col.html", sid=sid, source=source)


def delete_collection(source_id):
    # Delete the source's collection of submissions
    job = worker.enqueue(store.delete_source_directory, source_id)

    # Delete the source's reply keypair
    crypto_util.delete_reply_keypair(source_id)

    # Delete their entry in the db
    source = get_source(source_id)
    db_session.delete(source)
    db_session.commit()
    return job


@app.route('/col/process', methods=('POST',))
@login_required
def col_process():
    actions = {'download-unread': col_download_unread,
               'download-all': col_download_all, 'star': col_star,
               'un-star': col_un_star, 'delete': col_delete}
    if 'cols_selected' not in request.form:
        flash('No collections selected!', 'error')
        return redirect(url_for('index'))

    # getlist is cgi.FieldStorage.getlist
    cols_selected = request.form.getlist('cols_selected')
    action = request.form['action']

    if action not in actions:
        return abort(500)

    method = actions[action]
    return method(cols_selected)


def col_download_unread(cols_selected):
    """Download all unread submissions from all selected sources."""
    submissions = []
    for sid in cols_selected:
        id = Source.query.filter(Source.filesystem_id == sid).one().id
        submissions += Submission.query.filter(Submission.downloaded == False,
                                               Submission.source_id == id).all()
    if submissions == []:
        flash("No unread submissions in collections selected!", "error")
        return redirect(url_for('index'))
    return download("unread", submissions)


def col_download_all(cols_selected):
    """Download all submissions from all selected sources."""
    submissions = []
    for sid in cols_selected:
        id = Source.query.filter(Source.filesystem_id == sid).one().id
        submissions += Submission.query.filter(Submission.source_id == id).all()
    return download("all", submissions)


def col_star(cols_selected):
    for sid in cols_selected:
        make_star_true(sid)

    db_session.commit()
    return redirect(url_for('index'))


def col_un_star(cols_selected):
    for source_id in cols_selected:
        make_star_false(source_id)

    db_session.commit()
    return redirect(url_for('index'))


@app.route('/col/delete/<sid>', methods=('POST',))
@login_required
def col_delete_single(sid):
    """deleting a single collection from its /col page"""
    source = get_source(sid)
    delete_collection(sid)
    flash(
        "%s's collection deleted" %
        (source.journalist_designation,), "notification")
    return redirect(url_for('index'))


def col_delete(cols_selected):
    """deleting multiple collections from the index"""
    if len(cols_selected) < 1:
        flash("No collections selected to delete!", "error")
    else:
        for source_id in cols_selected:
            delete_collection(source_id)
        flash("%s %s deleted" % (
            len(cols_selected),
            "collection" if len(cols_selected) == 1 else "collections"
        ), "notification")

    return redirect(url_for('index'))


@app.route('/col/<sid>/<fn>')
@login_required
def download_single_submission(sid, fn):
    """Sends a client the contents of a single submission."""
    if '..' in fn or fn.startswith('/'):
        abort(404)

    try:
        Submission.query.filter(
            Submission.filename == fn).one().downloaded = True
        db_session.commit()
    except NoResultFound as e:
        app.logger.error("Could not mark " + fn + " as downloaded: %s" % (e,))

    return send_file(store.path(sid, fn), mimetype="application/pgp-encrypted")


@app.route('/reply', methods=('POST',))
@login_required
def reply():
    """Attempt to send a Reply from a Journalist to a Source. Empty
    messages are rejected, and an informative error message is flashed
    on the client. In the case of unexpected errors involving database
    transactions (potentially caused by racing request threads that
    modify the same the database object) logging is done in such a way
    so as not to write potentially sensitive information to disk, and a
    generic error message is flashed on the client.

    Returns:
       flask.Response: The user is redirected to the same Source
           collection view, regardless if the Reply is created
           successfully.
    """
    msg = request.form['msg']
    # Reject empty replies
    if not msg:
        flash("You cannot send an empty reply!", "error")
        return redirect(url_for('col', sid=g.sid))

    g.source.interaction_count += 1
    filename = "{0}-{1}-reply.gpg".format(g.source.interaction_count,
                                          g.source.journalist_filename)
    crypto_util.encrypt(msg,
                        [crypto_util.getkey(g.sid), config.JOURNALIST_KEY],
                        output=store.path(g.sid, filename))
    reply = Reply(g.user, g.source, filename)

    try:
        db_session.add(reply)
        db_session.commit()
    except Exception as exc:
        flash("An unexpected error occurred! Please check the application "
              "logs or inform your adminstrator.", "error")
        # We take a cautious approach to logging here because we're dealing
        # with responses to sources. It's possible the exception message could
        # contain information we don't want to write to disk.
        app.logger.error(
            "Reply from '{}' (id {}) failed: {}!".format(g.user.username,
                                                         g.user.id,
                                                         exc.__class__))
    else:
        flash("Thanks! Your reply has been stored.", "notification")
    finally:
        return redirect(url_for('col', sid=g.sid))


@app.route('/regenerate-code', methods=('POST',))
@login_required
def generate_code():
    original_journalist_designation = g.source.journalist_designation
    g.source.journalist_designation = crypto_util.display_id()

    for item in g.source.collection:
        item.filename = store.rename_submission(
            g.sid,
            item.filename,
            g.source.journalist_filename)
    db_session.commit()

    flash(
        "The source '%s' has been renamed to '%s'" %
        (original_journalist_designation,
         g.source.journalist_designation),
        "notification")
    return redirect('/col/' + g.sid)


@app.route('/download_unread/<sid>')
@login_required
def download_unread_sid(sid):
    id = Source.query.filter(Source.filesystem_id == sid).one().id
    submissions = Submission.query.filter(Submission.source_id == id,
                                          Submission.downloaded == False).all()
    if submissions == []:
        flash("No unread submissions for this source!")
        return redirect(url_for('col', sid=sid))
    source = get_source(sid)
    return download(source.journalist_filename, submissions)


@app.route('/bulk', methods=('POST',))
@login_required
def bulk():
    action = request.form['action']

    doc_names_selected = request.form.getlist('doc_names_selected')
    selected_docs = [doc for doc in g.source.collection
                     if doc.filename in doc_names_selected]
    if selected_docs == []:
        if action == 'download':
            flash("No collections selected to download!", "error")
        elif action in ('delete', 'confirm_delete'):
            flash("No collections selected to delete!", "error")
        return redirect(url_for('col', sid=g.sid))

    if action == 'download':
        source = get_source(g.sid)
        return download(source.journalist_filename, selected_docs)
    elif action == 'delete':
        return bulk_delete(g.sid, selected_docs)
    elif action == 'confirm_delete':
        return confirm_bulk_delete(g.sid, selected_docs)
    else:
        abort(400)


def confirm_bulk_delete(sid, items_selected):
    return render_template('delete.html',
                           sid=sid,
                           source=g.source,
                           items_selected=items_selected)


def bulk_delete(sid, items_selected):
    for item in items_selected:
        item_path = store.path(sid, item.filename)
        worker.enqueue(store.secure_unlink, item_path)
        db_session.delete(item)
    db_session.commit()

    flash(
        "Submission{} deleted.".format(
            "s" if len(items_selected) > 1 else ""),
        "notification")
    return redirect(url_for('col', sid=sid))


def download(zip_basename, submissions):
    """Send client contents of zipfile *zip_basename*-<timestamp>.zip
    containing *submissions*. The zipfile, being a
    :class:`tempfile.NamedTemporaryFile`, is stored on disk only
    temporarily.

    :param str zip_basename: The basename of the zipfile download.

    :param list submissions: A list of :class:`db.Submission`s to
                             include in the zipfile.
    """
    zf = store.get_bulk_archive(submissions,
                                zip_directory=zip_basename)
    attachment_filename = "{}--{}.zip".format(
        zip_basename, datetime.utcnow().strftime("%Y-%m-%d--%H-%M-%S"))

    # Mark the submissions that have been downloaded as such
    for submission in submissions:
        submission.downloaded = True
    db_session.commit()

    return send_file(zf.name, mimetype="application/zip",
                     attachment_filename=attachment_filename,
                     as_attachment=True)


@app.route('/flag', methods=('POST',))
@login_required
def flag():
    g.source.flagged = True
    db_session.commit()
    return render_template('flag.html', sid=g.sid,
                           codename=g.source.journalist_designation)


if __name__ == "__main__":  # pragma: no cover
    debug = getattr(config, 'env', 'prod') != 'prod'
    app.run(debug=debug, host='0.0.0.0', port=8081)
