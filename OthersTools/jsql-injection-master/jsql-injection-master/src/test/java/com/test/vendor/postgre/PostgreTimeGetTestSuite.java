package com.test.vendor.postgre;

import org.junit.BeforeClass;
import org.junit.Test;

import com.jsql.model.InjectionModel;
import com.jsql.model.MediatorModel;
import com.jsql.model.exception.InjectionFailureException;
import com.jsql.model.exception.JSqlException;
import com.jsql.model.injection.method.MethodInjection;
import com.jsql.model.injection.strategy.StrategyInjection;
import com.jsql.util.ConnectionUtil;
import com.jsql.view.terminal.SystemOutTerminal;
import com.test.AbstractTestSuite;

public class PostgreTimeGetTestSuite extends ConcretePostgreTestSuite {

    @BeforeClass
    public static void initialize() throws InjectionFailureException {
        InjectionModel model = new InjectionModel();
        MediatorModel.register(model);
        model.displayVersion();

        MediatorModel.model().addObserver(new SystemOutTerminal());

        ConnectionUtil.setUrlBase("http://"+ AbstractTestSuite.HOSTNAME +"/pg_simulate_get.php");
        ConnectionUtil.setQueryString("?lib=1");
        ConnectionUtil.setMethodInjection(MethodInjection.QUERY);

        MediatorModel.model().beginInjection();

        MediatorModel.model().setStrategy(StrategyInjection.TIME);
    }

    @Override
    @Test
    public void listColumns() throws JSqlException {
        LOGGER.info("Ignore: too slow");
    }

    @Override
    @Test
    public void listTables() throws JSqlException {
        LOGGER.info("Ignore: too slow");
    }
    
}
