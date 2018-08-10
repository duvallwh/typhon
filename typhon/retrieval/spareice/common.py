"""Retrieval of IWP from passive radiometers

This class is a reimplementation of the SPARE-ICE product introduced by
Holl et al. 2014.

References:
    TODO: Add reference.

Examples:

    .. code-block:: python

    from typhon.files import AVHRR_GAC_HDF, CloudSat, FileSet, MHS_HDF
    from typhon.retrieval import SPAREICE

    cloudsat = FileSet(...)
    mhs = FileSet(...)
    avhrr = FileSet(...)

    spareice = SPAREICE(
        file="spareice.json",
    )

    # Either we have already collocated, then we can use the files directly for
    # the training or SPARE-ICE should create the training dataset by itself
    # (should collocate by itself).
    data = spareice.prepare_training_data(
        # Do we have already collocations with all instruments? Put them here:
        collocations=...,
        # OR
        cloudsat=cloudsat, mhs=mhs, avhrr=avhrr,
        # Which time period should be used for training?
        start=..., end=...,
    )

    # To save time and memory space, we can store the current object with
    # the training data to the disk and reuse it later directly. So, we do not
    # have to call spareice.prepare_training_data again:
    data.to_netcdf("spareice_training.nc")

    # Train SPARE-ICE with the data
    spareice.train(data, test_ratio=0.2)

    # After training, we can use the SPARE-ICE retrieval:
    spareice.retrieve(
        # Do we have already collocations with MHS and AVHRR? Put them here:
        collocations=...,
        # Otherwise we can put here each fileset and create collocations
        # on-the-fly
        mhs=mhs, avhrr=avhrr,
        # Which time period should be used for retrieving?
        start=..., end=...,
        output=...,
    )
"""
from ast import literal_eval
import itertools
import os
from os.path import join, dirname
import warnings

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from sklearn.metrics import confusion_matrix
from sklearn.model_selection import GridSearchCV
from sklearn.neural_network import MLPRegressor
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import RobustScaler
from sklearn.tree import DecisionTreeClassifier
from typhon.collocations import collapse, Collocations, Collocator
from typhon.plots import binned_statistic, heatmap, styles, worldmap
from typhon.utils.timeutils import to_datetime

from ..common import RetrievalProduct

# Use the typhon style for all plots:
plt.style.use(styles('typhon'))

__all__ = [
    'SPAREICE',
]

# The path to the standard weights:
PARAMETERS_DIR = join(dirname(__file__), 'parameters')
STANDARD_FILE = join(PARAMETERS_DIR, "standard.json")


class SPAREICE:
    """Retrieval of IWP from passive radiometers

    Examples:
    .. code-block:: python

        import pandas as pd
        from typhon.retrieval import SPAREICE

        # Create a SPARE-ICE object with the standard weights
        spareice = SPAREICE()

        # Print the required input fields
        print(spareice.inputs)

        # If you want to know the input fields for the each component, IWP
        # regressor and ice cloud classifier, you can get them like this:
        print(spareice.iwp.inputs)  # Inputs from IWP regressor
        print(spareice.ice_cloud.inputs)  # Inputs from ice cloud classifier

        # If you have yur own input data, you can use :meth:`retrieve` to run
        # SPARE-ICE on it.
        data = pd.DataFrame(...)
        retrieved = spareice.retrieve(data)

        # If your data directly comes from collocations between MHS and AVHRR,
        # you can use ::meth:`convert_collocated_data` to make it SPARE-ICE
        # compatible.
        collocations = Collocator().collocate(mhs_data, avhrr_data, ...)
        standardized_data = self.standardize_collocations(collocations)
        retrieved = spareice.retrieve(standardized_data)
    """

    def __init__(self, file=None, collocator=None, processes=10, verbose=2):
        """Initialize a SPAREICE object

        Args:
            file: A JSON file with the coefficients of SPAREICE. If not given,
                the standard configuration will be loaded.
            collocator: SPARE-ICE requires a collocator when it should be
                generated from filesets. You can pass your own
                :class:`Collocator` object here if you want.
            processes: Number of processes to parallelize the training or
                collocation search. 10 is the default. Best value depends on
                your machine.
            verbose: An integer value. The higher the value, the more debug
                messages are printed.
        """

        self.verbose = verbose
        self.processes = processes
        self.name = "SPARE-ICE"

        if collocator is None:
            self.collocator = Collocator(verbose=verbose)
        else:
            self.collocator = collocator

        # SPARE-ICE consists of two retrievals: one neural network for the IWP
        # and one decision tree classifier for the ice cloud flag
        self._iwp = None
        self._ice_cloud = None

        # The users can load SPARE-ICE from their own training or the standard
        # parameters:
        if file is None:
            try:
                self.load(STANDARD_FILE)
            except Exception as e:
                warnings.warn(
                    "Could not load the standard parameters of SPARE-ICE!\n"
                    "You need to train SPARE-ICE by yourself."
                )
                warnings.warn(str(e))
                self._iwp = RetrievalProduct()
                self._ice_cloud = RetrievalProduct()
        else:
            self.load(file)

    def _debug(self, msg):
        if self.verbose > 1:
            print(f"[{self.name}] {msg}")

    def _info(self, msg):
        if self.verbose > 0:
            print(f"[{self.name}] {msg}")

    def _iwp_trainer(self, processes, cv_folds):
        """Return the default trainer for the IWP retrieval
        """
        # Estimators are normally objects that have a fit and predict method
        # (e.g. MLPRegressor from sklearn). To make their training easier we
        # scale the input data in advance. With Pipeline objects from sklearn
        # we can combine such steps easily since they behave like an
        # estimator object as well.
        estimator = Pipeline([
            # SVM or NN work better if we have scaled the data in the first
            # place. MinMaxScaler is the simplest one. RobustScaler or
            # StandardScaler could be an alternative.
            ("scaler", RobustScaler(quantile_range=(15, 85))),
            # The "real" estimator:
            ("estimator", MLPRegressor(max_iter=3400)),
        ])

        # To optimize the results, we try different hyper parameters by
        # using a grid search
        hidden_layer_sizes = [
            (15, 10, 3), #  (13, 10),
        ]
        hyper_parameter = [
            {   # Hyper parameter for lbfgs solver
                'estimator__solver': ['lbfgs'],
                'estimator__activation': ['tanh'],
                'estimator__hidden_layer_sizes': hidden_layer_sizes,
                'estimator__random_state': [0, 42, 100, 3452],
                'estimator__alpha': [0.1],
            },
        ]

        return GridSearchCV(
            estimator, hyper_parameter, refit=True,
            n_jobs=processes, cv=cv_folds, verbose=self.verbose,
        )

    @staticmethod
    def _ice_cloud_trainer():
        # As simple as it is. We do not need a grid search trainer for the DTC
        # since it has already a good performance.
        return DecisionTreeClassifier(
            max_depth=12, random_state=5,
        )

    @property
    def inputs(self):
        """Return the input fields of the current configuration"""
        return list(set(self.iwp.inputs) | set(self.ice_cloud.inputs))

    @property
    def iwp(self):
        """Return the IWP regressor of SPARE-ICE"""
        return self._iwp

    @property
    def ice_cloud(self):
        """Return the ice cloud classifier of SPARE-ICE"""
        return self._ice_cloud

    def load(self, filename):
        """Load SPARE-ICE from a json file

        Args:
            filename: Path and name of the file.

        Returns:
            None
        """
        with open(filename, 'r') as infile:
            parameters = literal_eval(infile.read())
            self._iwp = RetrievalProduct.from_dict(
                parameters["iwp"]
            )
            self._ice_cloud = RetrievalProduct.from_dict(
                parameters["ice_cloud"]
            )

    def save(self, filename):
        """Save SPARE-ICE to a json file

        Notes:
            The output format is not standard json!

        Args:
            filename: Path and name of the file.

        Returns:
            None
        """
        with open(filename, 'w') as outfile:
            dictionary = {
                "iwp": self.iwp.to_dict(),
                "ice_cloud": self.ice_cloud.to_dict(),
            }
            outfile.write(repr(dictionary))

    @staticmethod
    def standardize_collocations(data, fields=None):
        """Convert collocation fields to standard SPARE-ICE fields.

        Args:
            data: A xarray.Dataset object with collocations either amongst
                2C-ICE, MHS & AVHRR or MHS & AVHRR.
            fields (optional): Fields that will be selected from the
                collocations. If None (default), all fields will be selected.

        Returns:
            A pandas.DataFrame with all selected fields.
        """
        # Check whether the data is coming from a twice-collocated dataset:
        if "MHS_2C-ICE/MHS/scnpos" in data.variables:
            prefix = "MHS_2C-ICE/"
        else:
            prefix = ""

        # The keys of this dictionary are the new names, while the values are
        # old the names coming from the original collocations. If the value is
        # a list, the variable is 2-dimensional. The first element is the old
        # name, and the rest is the dimnesion that should be selected.
        mapping = {
            "mhs_channel1": [
                f"{prefix}MHS/Data/btemps", f"{prefix}MHS/channel", 0
            ],
            "mhs_channel2": [
                f"{prefix}MHS/Data/btemps", f"{prefix}MHS/channel", 1
            ],
            "mhs_channel3": [
                f"{prefix}MHS/Data/btemps", f"{prefix}MHS/channel", 2
            ],
            "mhs_channel4": [
                f"{prefix}MHS/Data/btemps", f"{prefix}MHS/channel", 3
            ],
            "mhs_channel5": [
                f"{prefix}MHS/Data/btemps", f"{prefix}MHS/channel", 4
            ],
            "lat": "lat",
            "lon": "lon",
            "time": "time",
            "mhs_scnpos": f"{prefix}MHS/scnpos",
            "solar_azimuth_angle":
                f"{prefix}MHS/Geolocation/Solar_azimuth_angle",
            "solar_zenith_angle":
                f"{prefix}MHS/Geolocation/Solar_zenith_angle",
            "satellite_azimuth_angle":
                f"{prefix}MHS/Geolocation/Satellite_azimuth_angle",
            "satellite_zenith_angle":
                f"{prefix}MHS/Geolocation/Satellite_zenith_angle",
            "avhrr_channel1": [
                "AVHRR/Data/btemps_mean", "AVHRR/channel", 0
            ],
            "avhrr_channel2": [
                "AVHRR/Data/btemps_mean", "AVHRR/channel", 1
            ],
            "avhrr_channel3": [
                "AVHRR/Data/btemps_mean", "AVHRR/channel", 2
            ],
            "avhrr_channel4": [
                "AVHRR/Data/btemps_mean", "AVHRR/channel", 3
            ],
            "avhrr_channel5": [
                "AVHRR/Data/btemps_mean", "AVHRR/channel", 4
            ],
            "avhrr_channel1_std": [
                "AVHRR/Data/btemps_std", "AVHRR/channel", 0
            ],
            "avhrr_channel2_std": [
                "AVHRR/Data/btemps_std", "AVHRR/channel", 1
            ],
            "avhrr_channel3_std": [
                "AVHRR/Data/btemps_std", "AVHRR/channel", 2
            ],
            "avhrr_channel4_std": [
                "AVHRR/Data/btemps_std", "AVHRR/channel", 3
            ],
            "avhrr_channel5_std": [
                "AVHRR/Data/btemps_std", "AVHRR/channel", 4
            ],
            "iwp_number": "MHS_2C-ICE/2C-ICE/ice_water_path_number",
            "iwp_std": "MHS_2C-ICE/2C-ICE/ice_water_path_std",
        }

        # These fields need a special treatment
        special_fields = ["avhrr_tir_diff", "iwp", "ice_cloud"]

        # Default - take all fields:
        if fields is None:
            fields = list(mapping.keys()) + special_fields

        return_data = {}
        for field in fields:
            if field in special_fields:
                # We will do this later:
                continue
            elif field not in mapping:
                raise KeyError(f"I do not know the field '{field}'!")

            key = mapping[field]
            if isinstance(key, list):
                return_data[field] = data[key[0]].isel(
                    **{key[1]: key[2]}
                )
            else:
                return_data[field] = data[key]

        return_data = pd.DataFrame(return_data)

        if "avhrr_tir_diff" in fields:
            return_data["avhrr_tir_diff"] = \
                return_data["avhrr_channel5"] - return_data["avhrr_channel4"]
        if "iwp" in fields:
            # We transform the IWP to log space because it is better for the
            # ANN training. Zero values might trigger warnings and
            # result in -INF. However, we cannot drop them because the ice
            # cloud classifier needs zero values for its training.
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                return_data["iwp"] = np.log10(
                    data["MHS_2C-ICE/2C-ICE/ice_water_path_mean"]
                )
                return_data["iwp"].replace(
                    [-np.inf, np.inf], np.nan, inplace=True
                )
        if "ice_cloud" in fields:
            return_data["ice_cloud"] = \
                data["MHS_2C-ICE/2C-ICE/ice_water_path_mean"] > 0

        return return_data

    def retrieve(self, data, as_log10=False):
        """Retrieve SPARE-ICE for the input variables

        Args:
            data: A pandas.DataFrame object with required input fields (see
                above) or a xarray.Dataset if `from_collocations` is True.
            as_log10: If true, the retrieved IWP will be returned as logarithm
                of base 10.

        Returns:
            A pandas DataFrame object with the retrieved IWP and ice cloud
            flag.
        """

        # Retrieve the ice water path:
        retrieved = self.iwp.retrieve(data[self.iwp.inputs])
        if not as_log10 and retrieved is not None:
            retrieved["iwp"] = 10**retrieved["iwp"]

        # Retrieve the ice cloud flag:
        retrieved = retrieved.join(
            self.ice_cloud.retrieve(data[self.ice_cloud.inputs]),

        )

        return retrieved

    def _get_retrieval_data(self, inputs, start, end, processes):
        if isinstance(inputs, Collocations):
            yield from inputs.icollect(start=start, end=end)
        elif len(inputs) == 2:
            # Collocate MHS and AVHRR on-the-fly:

            names = set(fileset.name for fileset in inputs)
            if "MHS" not in names or "AVHRR" not in names:
                raise ValueError(
                    "You must name the input filesets MHS and AVHRR! Their "
                    f"current names are: {names}"
                )

            data_iterator = self.collocator.collocate_filesets(
                inputs, start=start, end=end, processes=processes,
                max_interval="30s", max_distance="7.5 km",
            )
            for data, attributes in data_iterator:
                yield collapse(data, reference="MHS"), attributes
        else:
            raise ValueError(
                "You need to pass a Collocations object or a list with a MHS "
                "and AVHRR fileset!"
            )

    def retrieve_from_filesets(
            self, inputs, output, start=None, end=None, processes=None,
            post_reader=None,
    ):
        """Retrieve SPARE-ICE from all files in a fileset

        You can use this either with already collocated MHS and AVHRR data
        (pass the :class:`Collocations` object via `inputs`) or you let MHS and
        AVHRR collocate on-the-fly by passing the filesets with the raw data
        (pass two filesets as list via `inputs`).

        Args:
            inputs: Can be :class:`Collocations` or a list with
                :class:`~typhon.files.fileset.FileSet` objects. If it is a
                :class:`Collocations` object, all files from them are processed
                and use as input for SPARE-ICE.
            output: Must be a path with placeholders or a :class:`FileSet`
                object where the output files should be stored.
            start: Start date either as datetime object or as string
                ("YYYY-MM-DD hh:mm:ss"). Year, month and day are required.
                Hours, minutes and seconds are optional. If not given, it is
                datetime.min per default.
            end: End date. Same format as "start". If not given, it is
                datetime.max per default.
            processes: Number of processes to parallelize the collocation
                search. If not set, the value from the initialization is
                taken.
            post_reader:

        Returns:
            None
        """
        if processes is None:
            processes = self.processes

        data_iterator = self._get_retrieval_data(
            inputs, start, end, processes
        )

        for data, attributes in data_iterator:
            self._info(
                f"Retrieve SPARE-ICE for {data.attrs['start_time']} to "
                f"{data.attrs['end_time']}"
            )

            if post_reader is not None:
                data = post_reader(data, attributes)

            # Remove NaNs from the data:
            data = data.dropna(dim="collocation")

            retrieved = self.retrieve(
                self.convert_collocated_data(data)
            )

            if retrieved is None:
                continue

            retrieved = retrieved.to_xarray()
            retrieved.rename({"index": "collocation"}, inplace=True)
            retrieved = retrieved.drop("collocation")

            # Add more information:
            retrieved["iwp"].attrs = {
                "units": "g/m^2",
                "name": "Ice Water Path",
                "description": "Ice Water Path (retrieved by SPARE-ICE)."
            }
            retrieved["ice_cloud"].attrs = {
                "units": "boolean",
                "name": "Ice Cloud Flag",
                "description": "True if pixel contains an ice cloud (retrieved"
                               " by SPARE-ICE)."
            }
            retrieved["lat"] = data["lat"]
            retrieved["lon"] = data["lon"]
            retrieved["time"] = data["time"]
            retrieved["scnpos"] = data["MHS/scnpos"]

            filename = output.get_filename(
                [to_datetime(data.attrs["start_time"]),
                 to_datetime(data.attrs["end_time"])], fill=attributes
            )

            # Write the data to the file.
            self._info(f"Store SPARE-ICE to \n{filename}")
            output.write(retrieved, filename)

    def score(self, data):
        """Calculate the score of SPARE-ICE on testing data

        Args:
            data: A pandas.DataFrame object with the required input fields.

        Returns:
            The score for the IWP regressor and the score for the ice cloud
            classifier.
        """

        ice_cloud_score = self.ice_cloud.score(
            data[self.ice_cloud.inputs], data[self.ice_cloud.outputs]
        )

        # We cannot allow NaN or Inf (resulting from transformation to
        # log space)
        data = data.dropna()
        iwp_score = self.iwp.score(
            data[self.iwp.inputs], data[self.iwp.outputs]
        )
        return iwp_score, ice_cloud_score

    def train(self, data, iwp_inputs=None, ice_cloud_inputs=None,
              processes=None, cv_folds=None):
        """Train SPARE-ICE with data

        This trains the IWP regressor and ice cloud classifier.

        Args:
            data: A pandas.DataFrame object with the required input fields.
            iwp_inputs: A list with the input field names for the IWP
                regressor. If this is None, the IWP regressor won't be trained.
            ice_cloud_inputs: A list with the input field names for the ice
                cloud classifier. If this is None, the ice cloud classifier
                won't be trained.
            processes: Number of processes to parallelize the regressor
                training. If not set, the value from the initialization is
                taken.
            cv_folds: Number of folds used for cross-validation. Default is 5.
                The higher the number the more data is used for training but
                the runtime increases. Good values are between 3 and 10.

        Returns:
            None
        """

        if iwp_inputs is None and ice_cloud_inputs is None:
            raise ValueError("Either fields for the IWP regressor or ice "
                             "cloud classifier must be given!")

        if ice_cloud_inputs is not None:
            self._info("Train SPARE-ICE - ice cloud classifier")
            score = self.ice_cloud.train(
                self._ice_cloud_trainer(),
                data[ice_cloud_inputs], data[["ice_cloud"]],
            )
            self._info(f"Ice cloud classifier training score: {score:.2f}")

        if iwp_inputs is not None:
            self._info("Train SPARE-ICE - IWP regressor")

            # We cannot allow NaN or Inf (resulting from transformation to
            # log space)
            data = data.dropna()

            if processes is None:
                processes = self.processes
            if cv_folds is None:
                cv_folds = 5

            trainer = self._iwp_trainer(processes, cv_folds)
            score = self.iwp.train(
                trainer, data[iwp_inputs], data[["iwp"]],
            )
            self._info(f"IWP regressor training score: {score:.2f}")
            self._training_report(trainer)

    @staticmethod
    def _training_report(trainer):
        if not hasattr(trainer, "cv_results_"):
            return

        print("Best parameters found on training dataset:\n",
              trainer.best_params_)

        means = trainer.cv_results_['mean_test_score']
        stds = trainer.cv_results_['std_test_score']
        for mean, std, params in zip(means, stds, trainer.cv_results_['params']):  # noqa
            print("%0.3f (+/-%0.03f) for %r" % (mean, std * 2, params))

    def report(self, output_dir, experiment, data):
        """Test the performance of SPARE-ICE and plot it

        Args:
            output_dir: A path to a directory (does not need to exist). A
                subdirectory named `experiment` will be created there. All
                plots are stored to it.
            experiment: A name for the experiment as a string. Will be included
                in the title of the plots and used as name for the subdirectory
                in `output_dir`.
            data: A pandas.DataFrame object with the required input fields.

        Returns:
            None
        """
        # Create the output directory:
        output_dir = join(output_dir, experiment)
        os.makedirs(output_dir, exist_ok=True)

        # Run SPARE-ICE!
        retrieved = self.retrieve(data, as_log10=True)

        # We are going to plot the performance of the two retrievals:
        self._report_iwp(output_dir, experiment, data, retrieved)
        self._report_ice_cloud(output_dir, experiment, data, retrieved)

    def _report_iwp(self, output_dir, experiment, test, retrieved):
        """Create and store the plots for IWP regressor"""

        # Plot the heatmap with the retrieved IWPs
        fig, ax = plt.subplots(figsize=(10, 8))
        scat = heatmap(
            test.iwp,
            retrieved.iwp,
            bins=50, range=[[-1, 4], [-1, 4]],
            cmap="density"
        )
        ax.set_xlabel("log10 IWP (2C-ICE) [g/m^2]")
        ax.set_ylabel("log10 IWP (SPARE-ICE) [g/m^2]")
        ax.set_title(experiment)
        fig.colorbar(scat)
        fig.savefig(join(output_dir, "2C-ICE-SPAREICE_heatmap.png"))

        self._plot_scatter(
            experiment,
            join(output_dir, "2C-ICE-SPAREICE_scatter_{area}.png"),
            test.iwp, retrieved.iwp, test.sea_mask.values
        )

        # MFE plot with 2C-ICE on x-axis
        fe = 100 * (
                np.exp(np.abs(
                    np.log(
                        10 ** retrieved.iwp.values
                        / 10 ** test.iwp.values
                    )
                )) - 1
        )
        self._plot_error(
            experiment, join(output_dir, "2C-ICE-SPAREICE_mfe.png"),
            test.iwp.values,
            fe, test.sea_mask.values,
        )
        # MFE plot with latitude on x-axis
        self._plot_error(
            experiment, join(output_dir, "2C-ICE-SPAREICE_mfe_lat.png"),
            test.lat.values,
            fe, test.sea_mask.values,
            on_lat=True
        )

        # Plot the bias:
        bias = retrieved.iwp.values - test.iwp.values
        self._plot_error(
            experiment, join(output_dir, "2C-ICE-SPAREICE_bias.png"),
            test.iwp.values,
            bias, test.sea_mask.values,
            mfe=False,
        )
        # MFE plot with latitude on x-axis
        self._plot_error(
            experiment, join(output_dir, "2C-ICE-SPAREICE_bias_lat.png"),
            test.lat.values,
            bias, test.sea_mask.values,
            mfe=False, on_lat=True
        )

        self._plot_weights(
            experiment, join(output_dir, "SPAREICE_iwp_weights.png"),
        )

    @staticmethod
    def _plot_scatter(experiment, file, xdata, ydata, sea_mask):
        for area in ["all", "land", "sea"]:
            if area == "all":
                mask = slice(None, None, None)
            elif area == "land":
                mask = ~sea_mask
            else:
                mask = sea_mask

            fig, ax = plt.subplots(figsize=(10, 8))
            ax.scatter(
                xdata[mask], ydata[mask],
                s=1, alpha=0.6
            )
            ax.grid()
            ax.set_xlabel("log10 IWP (2C-ICE) [g/m^2]")
            ax.set_ylabel("log10 IWP (SPARE-ICE) [g/m^2]")
            ax.set_title(f"{experiment} - {area}")
            fig.savefig(file.format(area=area))

    @staticmethod
    def _plot_error(
            experiment, file, xdata, error, sea_mask, on_lat=False, mfe=True):

        fig, ax = plt.subplots(figsize=(10, 8))
        if on_lat:
            xlabel = "latitude"
            xrange = [-90, 90]
        else:
            xlabel = "log10 IWP (2C-ICE) [g/m^2]"
            xrange = [0, 4]

        if mfe:
            ax.set_ylabel("Median fractional error [%]")
            ax.set_ylim([0, 400])
            statistic = "median"
        else:
            ax.set_ylabel("$\Delta$ IWP (SPARE-ICE - 2C-ICE) [log 10 g/m^2]")
            statistic = "mean"

        for area in ["all", "land", "sea"]:
            if area == "all":
                mask = slice(None, None, None)
            elif area == "land":
                mask = ~sea_mask
            else:
                mask = sea_mask

            binned_statistic(
                xdata[mask], error[mask], statistic=statistic, bins=20,
                range=xrange, pargs={"marker": "o", "label": area}
            )

        ax.set_xlabel(xlabel)
        ax.grid()
        ax.legend(fancybox=True)
        ax.set_title(f"Experiment: {experiment}")
        fig.tight_layout()
        fig.savefig(file)

    def _plot_weights(self, title, file, layer_index=0, vmin=-5, vmax=5):
        import seaborn as sns
        sns.set_context("paper")

        layers = self.iwp.estimator.steps[-1][1].coefs_
        layer = layers[layer_index]
        f, ax = plt.subplots(figsize=(12, 10))
        weights = pd.DataFrame(layer)
        weights.index = self.iwp.inputs

        sns.set(font_scale=1.1)

        # Draw a heatmap with the numeric values in each cell
        sns.heatmap(
            weights, annot=True, fmt=".1f", linewidths=.5, ax=ax,
            square=True, cmap="difference", center=0, vmin=vmin, vmax=vmax,
            # annot_kws={"size":14},
        )
        ax.tick_params(labelsize=18)
        f.tight_layout()

    def _report_ice_cloud(self, output_dir, experiment, test, retrieved):
        # Confusion matrix:
        fig, ax = plt.subplots()
        cm = confusion_matrix(test.ice_cloud, retrieved.ice_cloud)
        img = self._plot_matrix(cm, classes=["Yes", "No"], normalize=True)
        fig.colorbar(img)
        ax.set_title("Ice Cloud Classifier - Performance")
        ax.set_ylabel('real ice cloud')
        ax.set_xlabel('predicted ice cloud')
        fig.tight_layout()
        fig.savefig(join(output_dir, "ice_cloud_confusion_matrix.png"))

        fig, ax = plt.subplots()
        ax.barh(
            np.arange(len(self.ice_cloud.inputs)),
            self.ice_cloud.estimator.feature_importances_
        )
        ax.set_yticks(np.arange(len(self.ice_cloud.inputs)))
        ax.set_yticklabels(self.ice_cloud.inputs)
        ax.set_xlabel("Importance")
        ax.set_ylabel("Input")
        ax.set_title("Ice Cloud Classifier - Importance")
        fig.savefig(join(output_dir, "ice_cloud_feature_importance.png"))

    @staticmethod
    def _plot_matrix(
            matrix, classes, normalize=False, ax=None, **kwargs
    ):
        """Plots the confusion matrix of
        Normalization can be applied by setting `normalize=True`.
        """
        if normalize:
            matrix = matrix.astype('float') / matrix.sum(axis=1)[:, np.newaxis]

        default_kwargs = {
            "cmap": "Blues",
            **kwargs
        }

        if ax is None:
            ax = plt.gca()

        img = ax.imshow(matrix, interpolation='nearest', **default_kwargs)
        tick_marks = np.arange(len(classes))
        ax.set_xticks(tick_marks)
        ax.set_xticklabels(classes, rotation=45)
        ax.set_yticks(tick_marks)
        ax.set_yticklabels(classes)

        fmt = '.2f' if normalize else 'd'
        thresh = matrix.max() / 2.
        for i, j in itertools.product(range(matrix.shape[0]),
                                      range(matrix.shape[1])):
            ax.text(j, i, format(matrix[i, j], fmt),
                    horizontalalignment="center",
                    color="white" if matrix[i, j] > thresh else "black")

        return img
