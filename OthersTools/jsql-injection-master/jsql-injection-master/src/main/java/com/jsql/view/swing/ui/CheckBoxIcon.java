package com.jsql.view.swing.ui;

import java.awt.Component;
import java.awt.Graphics;
import java.io.Serializable;

import javax.swing.ButtonModel;
import javax.swing.Icon;
import javax.swing.JCheckBoxMenuItem;
import javax.swing.plaf.UIResource;
import javax.swing.plaf.metal.MetalLookAndFeel;

import com.jsql.view.swing.panel.util.MetalUtilsCustom;

@SuppressWarnings("serial")
public class CheckBoxIcon implements Icon, UIResource, Serializable {

    protected int getControlSize() { return 13; }

    private void paintOceanIcon(Component c, Graphics g, int x, int y) {
        ButtonModel model = ((JCheckBoxMenuItem)c).getModel();

        g.translate(x, y);
        int w = this.getIconWidth();
        int h = this.getIconHeight();
        if ( model.isEnabled() ) {
            if (model.isPressed() && model.isArmed()) {
                g.setColor(MetalLookAndFeel.getControlShadow());
                g.fillRect(0, 0, w, h);
                g.setColor(MetalLookAndFeel.getControlDarkShadow());
                g.fillRect(0, 0, w, 2);
                g.fillRect(0, 2, 2, h - 2);
                g.fillRect(w - 1, 1, 1, h - 1);
                g.fillRect(1, h - 1, w - 2, 1);
            } else if (model.isRollover()) {
                MetalUtilsCustom.drawGradient(c, g, "CheckBox.gradient", 0, 0,
                                        w, h, true);
                g.setColor(MetalLookAndFeel.getControlDarkShadow());
                g.drawRect(0, 0, w - 1, h - 1);
                g.setColor(MetalLookAndFeel.getPrimaryControl());
                g.drawRect(1, 1, w - 3, h - 3);
                g.drawRect(2, 2, w - 5, h - 5);
            }
            else {
                MetalUtilsCustom.drawGradient(c, g, "CheckBox.gradient", 0, 0,
                                        w, h, true);
                g.setColor(MetalLookAndFeel.getControlDarkShadow());
                g.drawRect(0, 0, w - 1, h - 1);
            }
            g.setColor( MetalLookAndFeel.getControlInfo() );
        } else {
            g.setColor(MetalLookAndFeel.getControlDarkShadow());
            g.drawRect(0, 0, w - 1, h - 1);
        }
        g.translate(-x, -y);
        if (model.isSelected()) {
            this.drawCheck(g, x, y);
        }
    }
    
    protected void drawCheck(Graphics g, int x, int y) {
        int controlSize = this.getControlSize();
        g.fillRect( x+3, y+5, 2, controlSize-8 );
        g.drawLine( x+controlSize-4, y+3, x+5, y+controlSize-6 );
        g.drawLine( x+controlSize-4, y+4, x+5, y+controlSize-5 );
    }

    @Override
    public void paintIcon(Component c, Graphics g, int x, int y) {
        this.paintOceanIcon(c, g, x, y);
    }

    @Override
    public int getIconWidth() {
        return this.getControlSize();
    }

    @Override
    public int getIconHeight() {
        return this.getControlSize();
    }
    
} // End class CheckBoxIcon