"""
Self-Exciting Threshold Autoregression

References
----------

Hansen, Bruce. 1999.
"Testing for Linearity."
Journal of Economic Surveys 13 (5): 551-576.
"""

from __future__ import division
import numpy as np
import statsmodels.tsa.base.tsa_model as tsbase
from statsmodels.tsa.tsatools import add_constant, lagmat
from statsmodels.regression.linear_model import OLS


class InvalidRegimeError(ValueError):
    pass


class SETAR(tsbase.TimeSeriesModel):
    """
    Self-Exciting Threshold Autoregressive Model

    Parameters
    ----------
    endog : array-like
        The endogenous variable.
    order : integer
        The order of the SETAR model, indication the number of regimes.
    ar_order : integer
        The order of the autoregressive parameters.
    delay : integer, optional
        The delay for the self-exciting threshold variable.
    thresholds : iterable, optional
        The threshold values separating the data into regimes.
    min_regime_frac : scalar, optional
        The minumum fraction of observations in each regime.
    max_delay : integer, optional
        The maximum delay parameter to consider if a grid search is used. If
        left blank, it is set to be the ar_order.
    threshold_grid_size : integer, optional
        The approximate number of elements in the threshold grid if a grid
        search is used.


    Notes
    -----
    threshold_grid_size is only approximate because it uses values from the
    threshold variable itself, approximately evenly spaced, and there may be a
    few more elements in the grid search than requested
    """

    # TODO are there too many parameters here?
    def __init__(self, endog, order, ar_order,
                 delay=None, thresholds=None, min_regime_frac=0.1,
                 max_delay=None, threshold_grid_size=100,
                 dates=None, freq=None, missing='none'):
        super(SETAR, self).__init__(endog, None, dates, freq)

        if delay is not None and delay < 1 or delay > ar_order:
            raise ValueError('Delay parameter must be greater than zero'
                             ' and less than ar_order. Got %d.' % delay)

        # Unsure of statistical properties if length of sample changes when
        # estimating hyperparameters, which happens if delay can be greater
        # than ar_order, so that the number of initial observations changes
        if delay is None and max_delay > ar_order:
            raise ValueError('Maximum delay for grid search must not be '
                             ' greater than the autoregressive order.')

        if thresholds is not None and not len(thresholds)+1 == order:
            raise ValueError('Number of thresholds must match'
                             ' the order of the SETAR model')

        # Exogenous matrix
        self.exog = add_constant(lagmat(self.endog, ar_order))
        self.nobs_initial = ar_order
        self.nobs = len(self.endog) - ar_order

        # "Immutable" properties
        self.order = order
        self.ar_order = ar_order
        self.min_regime_frac = min_regime_frac
        self.min_regime_num = np.ceil(min_regime_frac * self.nobs)
        self.max_delay = max_delay if max_delay is not None else ar_order
        self.threshold_grid_size = threshold_grid_size

        # "Flexible" properties
        self.delay = delay
        self.thresholds = np.sort(thresholds)
        self.regimes = None

    def build_datasets(self, delay, thresholds, order=None):
        if order is None:
            order = self.order

        endog = self.endog[self.nobs_initial:, ]
        exog_transpose = self.exog[self.nobs_initial:, ].T
        threshold_var = self.endog[self.nobs_initial-delay:-delay, ]
        indicators = np.searchsorted(thresholds, threshold_var)

        k = self.ar_order + 1
        exog_list = []
        for i in range(order):
            in_regime = (indicators == i)

            if in_regime.sum() < self.min_regime_num:
                raise InvalidRegimeError('Regime %d has too few observations:'
                                         ' threshold values may need to be'
                                         ' adjusted' % i)

            exog_list.append(np.multiply(exog_transpose, indicators == i).T)

        exog = np.concatenate(exog_list, 1)

        return endog, exog

    def fit(self):
        """
        Fits SETAR() model using arranged autoregression.

        Returns
        -------
        statsmodels.tsa.arima_model.SETARResults class

        See also
        --------
        statsmodels.regression.linear_model.OLS : this estimates each regime
        SETARResults : results class returned by fit

        """

        if self.delay is None or self.thresholds is None:
            self.delay, self.thresholds = self.select_hyperparameters()

        endog, exog = self.build_datasets(self.delay, self.thresholds)

        return OLS(endog, exog).fit()

    def _grid_search_objective(self, delay, thresholds, XX, resids):
        """
        Objective function to maximize in SETAR(2) hyperparameter grid search

        Corresponds to f_2(\gamma, d) in Hansen (1999), but extended to any
        number of thresholds.
        """
        endog, exog = self.build_datasets(
            delay, thresholds, order=len(thresholds)+1
        )

        # Intermediate calculations
        k = self.ar_order+1
        X1 = exog[:, :-k]
        X = self.exog[self.nobs_initial:]
        X1X1 = X1.T.dot(X1)
        XX1 = X.T.dot(X1)
        Mn = np.linalg.inv(
            X1X1 - XX1.T.dot(XX).dot(XX1)
        )

        # Return objective
        return resids.T.dot(X1).dot(Mn).dot(X1.T).dot(resids)

    def _select_hyperparameters_grid(self, thresholds, threshold_grid_size,
                                     XX, resids, delay_grid=None):

        if delay_grid is None:
            delay_grid = range(2, self.max_delay+1)

        max_obj = 0
        params = (None, None)
        # Iterate over possible delay values
        for delay in delay_grid:

            # Build the appropriate threshold grid given delay
            threshold_var = np.unique(np.sort(self.endog[:-delay]))
            nobs = len(threshold_var)
            indices = np.arange(self.min_regime_num,
                                nobs - self.min_regime_num,
                                max(np.floor(nobs / threshold_grid_size), 1),
                                dtype=int)
            threshold_grid = threshold_var[indices]

            # Iterate over possible threshold values
            for threshold in threshold_grid:
                try:
                    obj = self._grid_search_objective(
                        delay, np.sort([threshold] + thresholds),
                        XX, resids
                    )
                    if obj > max_obj:
                        max_obj = obj
                        params = (delay, threshold)
                # Some threshold values don't allow enough values in each
                # regime; we just need to not select those thresholds
                except InvalidRegimeError:
                    pass

        return params, max_obj

    def select_hyperparameters(self):
        """
        Select delay and threshold hyperparameters via grid search
        """

        # Cache calculations
        endog = self.endog[self.nobs_initial:]
        exog = self.exog[self.nobs_initial:]
        XX = np.linalg.inv(exog.T.dot(exog))    # (X'X)^{-1}
        resids = endog - np.dot(                # SETAR(1) residuals
            exog,
            XX.dot(exog.T.dot(endog))
        )

        # Get threshold grid size
        threshold_grid_size = self.threshold_grid_size

        # Estimate the delay and an initial value for the dominant threshold
        thresholds = []
        params, min_obj = self._select_hyperparameters_grid(
            thresholds, threshold_grid_size, XX, resids
        )
        delay = params[0]
        thresholds.append(params[1])

        # Get remaining thresholds
        for i in range(2, self.order):

            # Get initial estimate of next threshold
            params, min_obj = self._select_hyperparameters_grid(
                thresholds, threshold_grid_size, XX, resids,
                delay_grid=[delay]
            )
            thresholds.append(params[1])

            # Iterate threshold selection to convergence
            proposed = thresholds[:]
            iteration = 0
            maxiter = 100
            while True:
                iteration += 1

                # Recalculate each threshold individually, holding the others
                # constant, starting at the first threshold
                for j in range(i):
                    params, min_obj = self._select_hyperparameters_grid(
                        thresholds[:j] + thresholds[j+1:],
                        threshold_grid_size, XX, resids,
                        delay_grid=[delay]
                    )
                    proposed[j] = params[1]

                # If the recalculation produced no change, we've converged
                if proposed == thresholds:
                    break
                # If convergence is happening fast enough
                if iteration > maxiter:
                    print ('Warning: Maximum number of iterations has been '
                           'exceeded.')
                    break

                thresholds = proposed[:]

        return delay, np.sort(thresholds)


class SETARResults:
    pass
