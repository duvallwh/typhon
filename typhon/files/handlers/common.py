from collections import defaultdict
from copy import copy
from datetime import datetime
from functools import wraps
import glob
from inspect import signature, ismethod
import os
import pickle
import warnings

import netCDF4
import pandas as pd
import xarray as xr

# The HDF4 file handler needs pyhdf, this might be very tricky to install if
# you cannot use anaconda. Hence, I do not want it to be a hard dependency:
pyhdf_is_installed = False
try:
    from pyhdf import HDF, VS, V
    from pyhdf.SD import SD, SDC
    pyhdf_is_installed = True
except ImportError:
    pass

__all__ = [
    'CSV',
    'FileHandler',
    'FileInfo',
    'HDF4',
    'NetCDF4',
    'Plotter',
    'expects_file_info',
    # 'Numpy',
    # 'Pickle',
    # 'XML'
]


def parametrized(dec):
    """A decorator for decorators that need parameters

    Do not think about this too long, it may cause headaches. Have a look at
    this instead: https://stackoverflow.com/a/26151604

    Args:
        dec: A decorator function

    Returns:
        The decoratored decorator function.
    """
    def layer(*args, **kwargs):
        def repl(f):
            return dec(f, *args, **kwargs)
        return repl
    return layer


@parametrized
def expects_file_info(method, pos=None, key=None):
    """Convert a method argument to a :class:`FileInfo` object

    This is a decorator function that can take parameters.

    If the argument is already a FileInfo object, nothing happens.

    Args:
        method: Method object that should be decorated.
        pos: The index of the file info in the positional argument list.
            Default is 1 (assumes to decorate a method).
        key: The key of the file info in the key word argument dict.

    Returns:
        The return value of the decorated method.

    Examples:

        .. code-block:: python

        @expects_file_info()(0)
        def read(file, *args, *kwargs):
            # file is a Fileinfo object now with the attribute path containing
            # "path/to/file.txt"

        read("path/to/file.txt")

    """

    if pos is None and key is None:
        pos = 1

    @wraps(method)
    def wrapper(*args, **kwargs):
        args = list(args)
        if args and pos is not None:
            if not isinstance(args[pos], FileInfo):
                args[pos] = FileInfo(args[pos])
        else:
            if not isinstance(kwargs[key], FileInfo):
                kwargs[key] = FileInfo(kwargs[key])

        return method(*args, **kwargs)
    return wrapper


def _xarray_rename_fields(dataset, mapping):
    if mapping is not None:
        # Maybe some variables should be renamed that are not in the
        # dataset any longer?
        names = set(dataset.dims.keys()) | set(dataset.variables.keys())

        mapping = {
            old_name: new_name
            for old_name, new_name in mapping.items()
            if old_name in names
        }

        dataset.rename(mapping, inplace=True)

    return dataset


class FileHandler:
    """Base file handler class.

    This can be used alone or with the Dataset classes. You can
    either initialize specific *reader* ,*info* or *writer* functions or
    you can inherit from this class and override its methods. If you need a
    very specialised and reusable file handler class, you should
    consider following the second approach.
    """

    # Flag whether this file handler supports reading from multiple files at
    # once, i.e. the read method accepts a list of file info objects as well.
    reads_multiple_files = False

    def __init__(
            self, reader=None, info=None, writer=None, data_merger=None,
            data_concatenator=None, **kwargs):
        """Initialize a filer handler object.

        Args:
            reader: Reference to a function that defines how to read a given
                file and returns an object with the read data. The function
                must accept a :class:`FileInfo` object as first parameter.
            info: Reference to a function that returns a :class:`FileInfo`
                object with information about the given file. You cannot use
                the :meth:`get_info` without setting this parameter. The
                function must accept a filename as string as first parameter.
            writer: Reference to a function that defines how to write the data
                to a file. The function must accept the data object as first
                and a :class:`FileInfo` object as second parameter.
            data_merger: Reference to a function that can merge objects
                returned by :meth:`read`.
            data_concatenator: Reference to a function that can concatenate
                objects returned by :meth:`read`.
        """

        self.reader = reader
        self.info = info
        self.writer = writer
        self.data_merger = data_merger
        self.data_concatenator = data_concatenator

        # If you want to ravel / flat the data coming from this file handler
        # (e.g., this is necessary for collocation routines), you need the
        # dimension names that you can stack on top each other.
        self.stack_dims = {}

    @expects_file_info()
    def get_info(self, filename, **kwargs):
        """Return a :class:`FileInfo` object with parameters about the
        file content.

        Notes:
            This is the base class method that does nothing per default.

        Args:
            filename: A string containing path and name or a :class:`FileInfo`
                object of the file of which to get the information about.
            **kwargs: Additional keyword arguments.

        Returns:
            A :class:`FileInfo` object.
        """
        if self.info is not None:
            # Some functions do not accept additional key word arguments (via
            # kwargs). And if they are methods, they accept an additional
            # "self" or "class" parameter.
            number_args = 1 + int(ismethod(self.info))
            if len(signature(self.info).parameters) > number_args:
                return self.info(filename, **kwargs)
            else:
                return self.info(filename)

        raise NotImplementedError(
            "This file handler does not support reading data from a file. You "
            "should use a different file handler.")

    @expects_file_info()
    def read(self, filename, **kwargs):
        """Open a file by its name, read its content and return it

        Notes:
            This is the base class method that does nothing per default.

        Args:
            filename: A string containing path and name or a :class:`FileInfo`
                object of the file from which to read.
            **kwargs: Additional key word arguments.

        Returns:
            An object containing the file's content.
        """
        if self.reader is not None:
            # Some functions do not accept additional key word arguments (via
            # kwargs). And if they are methods, they accept an additional
            # "self" or "class" parameter.
            number_args = 1 + int(ismethod(self.reader))
            if len(signature(self.reader).parameters) > number_args:
                return self.reader(filename, **kwargs)
            else:
                return self.reader(filename)

        raise NotImplementedError(
            "This file handler does not support reading data from a file. You "
            "should use a different file handler.")

    @expects_file_info(pos=2)
    def write(self, data, filename, **kwargs):
        """Store a data object to a file.

        Notes:
            This is the base class method that does nothing per default.

        Args:
            filename: A string containing path and name or a :class:`FileInfo`
                object to which to store the data. Existing files will be
                overwritten.
            data: Object with data (e.g. numpy array, etc.).

        Returns:
            None
        """
        if self.writer is not None:
            if len(signature(self.writer).parameters) > 2:
                self.writer(data, filename, **kwargs)
            else:
                self.writer(data, filename)

            return None

        raise NotImplementedError(
            "This file handler does not support writing data to a file. You "
            "should use a different file handler.")


class FileInfo(os.PathLike):
    """Container of information about a file (time coverage, etc.)

    This is a simple object that holds the path and name, time coverage and
    further attributes of a file. It fulfills the os.PathLike protocol, i.e.
    you can use it as filename argument for the most python functions.

    See this Example:

    .. code-block:: python

        # Initialise a FileInfo object that points to a file
        file_info = FileInfo(
            path="path/to/a/file.txt",
            # The time coverage of the file (needed by Dataset classes)
            times=[datetime(2018, 1, 1), datetime(2018, 1, 10)],
            # Additional attributes:
            attr={},
        )

        with open(file_info) as file:
            ...

        # If you need to access the path or other attributes directly, you can
        # do it like this:
        file_info.path
        file_info.times
        file_info.attr
    """
    def __init__(self, path=None, times=None, attr=None):
        """Initialise a FileInfo object.

        Args:
            path: Absolute path to a file.
            times: A list or tuple of two datetime objects indicating start and
                end time of the file.
            attr: A dictionary with further attributes.
        """
        super(FileInfo, self).__init__()

        self._path = None
        self.path = path

        self._times = None
        self.times = times

        if attr is None:
            self.attr = {}
        else:
            self.attr = attr

    def __eq__(self, other):
        return self.path == other.path and self.times == other.times

    def __fspath__(self):
        return self.path

    def __hash__(self):
        # With this we can use this FileInfo object also in a dictionary
        return hash(self.path)

    def __repr__(self):
        if self.attr:
            attr_string = "\n  Attributes:"
            for k, v in self.attr.items():
                attr_string += "\n    %s: %s" % (k, v)

        return "{}\n  Start: {}\n  End: {}{}".format(
            self.path, *self.times,
            attr_string if self.attr else "",
        )

    def __str__(self):
        return self.path

    def copy(self):
        return copy(self)

    @classmethod
    def from_json_dict(cls, json_dict):
        times = []
        for i in range(2):
            if json_dict["times"][i] is None:
                times.append([None])
            else:
                times.append(
                    datetime.strptime(
                        json_dict["times"][i], "%Y-%m-%dT%H:%M:%S.%f"),
                )

        return cls(json_dict["path"], times, json_dict["attr"])

    @property
    def path(self):
        return self._path

    @path.setter
    def path(self, value):
        if isinstance(value, FileInfo):
            raise ValueError("You cannot set path to a FileInfo object.")
        self._path = value

    @property
    def times(self):
        return self._times

    @times.setter
    def times(self, value):
        if value is None:
            self._times = [None, None]
        else:
            self._times = list(value)
            if len(self._times) != 2:
                raise ValueError("FileInfo.times can only be a list of two "
                                 "timestamps!")

    def update(self, other_info, ignore_none_time=True):
        """Update this object with another FileInfo object.

        Args:
            other_info: A FileInfo object.
            ignore_none_time: If the start time or end time of *other_info* is
                set to None, it does not overwrite the corresponding time of
                this object.

        Returns:
            None
        """
        self.attr.update(**other_info.attr)

        if other_info.times[0] is not None or not ignore_none_time:
            self.times[0] = other_info.times[0]
        if other_info.times[1] is not None or not ignore_none_time:
            self.times[1] = other_info.times[1]

    def to_json_dict(self):
        return {
            "path": self.path,
            "times": [
                self.times[0].strftime("%Y-%m-%dT%H:%M:%S.%f"),
                self.times[1].strftime("%Y-%m-%dT%H:%M:%S.%f")
            ],
            "attr": self.attr,
        }


class CSV(FileHandler):
    """File handler that can read / write data from / to a CSV file

    A CSV file is file containing data separated by commas (or by any other
    delimiter).
    """
    def __init__(self, info=None):
        """Initializes a CSV file handler class.

        Args:
            info: A function that returns a :class:`FileInfo` object of a
                given file.
        """
        # Call the base class initializer
        super().__init__(info=info)

    @expects_file_info()
    def read(self, file_info, fields=None, **kwargs):
        """Read a CSV file and return an xarray.Dataset with its content

        Args:
            file_info: Path and name of the file as string or FileInfo object.
            fields: Field that you want to extract from the file. If not given,
                all fields are going to be extracted.
            **kwargs: Additional keyword arguments for the pandas function
                `pandas.read_csv`. See for more details:
                https://pandas.pydata.org/pandas-docs/stable/generated/pandas.read_csv.html

        Returns:
            A xarray.Dataset object.
        """

        data = pd.read_csv(file_info.path, **kwargs).to_xarray()

        if fields is None:
            return data
        else:
            return data[fields]

    @expects_file_info(pos=2)
    def write(self, data, file_info, **kwargs):
        """Write a xarray.Dataset to a CSV file.

        Args:
            data: An DataGroup object that should be saved.
            file_info: Path and name of the file as string or FileInfo object.
            **kwargs: Additional keyword arguments for
                `pandas.Dataframe.to_csv`. See for more details:
                https://pandas.pydata.org/pandas-docs/stable/generated/pandas.DataFrame.to_csv.html

        Returns:
            None
        """
        data.to_dataframe().to_csv(file_info.path, **kwargs)


class HDF4(FileHandler):
    """File handler that can read data from a HDF4 file
    """
    def __init__(self, info=None):
        """Initializes a CSV file handler class.

        Args:
            info: A function that returns a :class:`FileInfo` object of a
                given file.
        """
        if not pyhdf_is_installed:
            raise ImportError("Could not import pyhdf, which is necessary for "
                              "reading HDF4 files!")

        # Call the base class initializer
        super().__init__(info=info)

    @expects_file_info()
    def read(self, file_info, fields=None, mapping=None):
        """Read and parse HDF4 files and load them to a xarray.Dataset

        Args:
            file_info: Path and name of the file as string or FileInfo object.
            fields: Field names that you want to extract from
                this file as a list.
            mapping: A dictionary that maps old field names to new field names.
                If given, `fields` must contain the old field names.

        Returns:
            A xarray.Dataset object.
        """

        if fields is None:
            raise NotImplementedError(
                "You have to set field names. Loading the complete file is not"
                " yet implemented!"
            )

        dataset = xr.Dataset()

        # Files in HDF4 format are not very pretty. This code is taken from
        # http://hdfeos.org/zoo/OTHER/2010128055614_21420_CS_2B-GEOPROF_GRANULE_P_R04_E03.hdf.py
        # and adapted by John Mrziglod.

        file = HDF.HDF(file_info.path)

        try:
            vs = file.vstart()

            for field in fields:
                # Add the field data to the dataset.
                dataset[field] = self._get_field(vs, field)
        except Exception as e:
            raise e
        finally:
            file.close()

        return _xarray_rename_fields(dataset, mapping)

    @staticmethod
    def _get_field(vs, field):
        field_id = vs.find(field)

        if field_id == 0:
            # Field was not found.
            warnings.warn(
                "Field '{0}' was not found!".format(field), RuntimeWarning
            )

        field_id = vs.attach(field_id)
        nrecs, _, _, _, _ = field_id.inquire()
        raw_data = field_id.read(nRec=nrecs)
        data = xr.DataArray(raw_data).squeeze()
        field_id.detach()
        return data


class NetCDF4(FileHandler):
    """File handler that can read / write data from / to a netCDF4 file
    """

    def __init__(self, **kwargs):
        """Initializes a NetCDF4 file handler class.

        Args:
            info: You cannot use the :meth:`get_info` without giving a
                function here that returns a FileInfo object.
        """
        # Call the base class initializer
        super().__init__(**kwargs)

        # The read method can handle multiple files
        self.reads_multiple_files = True

    @expects_file_info()
    def read(self, file_info, fields=None, mapping=None, **kwargs):
        """Read and parse NetCDF files and load them to a xarray.Dataset

        Args:
            file_info: Path and name of the file as string or FileInfo object.
                This can also be a tuple/list of file names or a path with
                asterisk (this is still not implemented!).
            groups: Groups that you want to import. Otherwise all groups are
                going to be imported.
            fields: List of field names that should be read. The other fields
                will be ignored. If `mapping` is given, this should contain the
                new field names.
            mapping: A dictionary which is used for renaming the fields. If
                given, `fields` must contain the old field names.
            **kwargs: Additional keyword arguments for
                :func:`xarray.decode_cf` such as `mask_and_scale`, etc.

        Returns:
            A xarray.Dataset object.
        """
        # xr.open_dataset does still not support loading all groups from a
        # file except a very cumbersome (and expensive) way by using the
        # parameter `group`. To avoid this, we load all groups and their
        # variables by using the netCDF4 directly and load them later into a
        # xarray dataset.

        with netCDF4.Dataset(file_info.path, "r") as root:
            dataset = xr.Dataset()
            self._load_group(dataset, None, root, fields)

            xr.decode_cf(dataset, **kwargs)

        return _xarray_rename_fields(dataset, mapping)

    @staticmethod
    def _load_group(ds, path, group, fields):
        if path is None:
            # The current group is the root group
            path = ""
            ds.attrs = dict(group.__dict__)
        else:
            path += "/"

        # Dimension (coordinate) mapping: A coordinate might be defined in a
        # group, then it is valid for this group only. Otherwise, the
        # coordinate from the parent group is taken.
        dim_map = {
            dim: dim if dim in group.variables else path + dim
            for dim in group.dimensions
        }

        # Load variables:
        try:
            for var_name, var in group.variables.items():
                if fields is None or path + var_name in fields:
                    dims = [dim_map[dim] for dim in var.dimensions]
                    ds[path + var_name] = dims, var[:], dict(var.__dict__)
        except RuntimeError:
            raise KeyError(f"Could not load the variable {path + var_name}!")

        # Do the same for all sub groups:
        for sub_group_name, sub_group in group.groups.items():
            NetCDF4._load_group(
                ds, path + sub_group_name, sub_group, fields
            )

    @expects_file_info(pos=2)
    def write(self, data, filename, **kwargs):
        """Save a xarray.Dataset to a NetCDF file

        Args:
            data: A xarray.Dataset object. It may contain 'pseudo' groups (i.e.
                variables with */* in their names). Those variables will be
                saved in subgroups.
            filename:
            **kwargs:

        Returns:

        """
        full_names = defaultdict(list)

        for full in data.variables:
            group, _ = self._split_path(full)
            full_names[group].append(full)

        # If we ware writing out multiple groups, we do not want to overwrite
        # the last file:
        user_mode = kwargs.pop("mode", "w")
        already_openend = False
        for group, variables in full_names.items():
            ds = data[variables]

            # Remove the group name from all variables:
            mapping = {
                full: self._split_path(full)[1]
                for full in ds.variables
            }
            ds.rename(mapping, inplace=True)

            # Do not forget the dimension names :-)
            for var in ds.variables.values():
                var.dims = [
                    self._split_path(dim)[1]
                    for dim in var.dims
                ]

            ds.to_netcdf(
                filename.path, group=group,
                mode="a" if already_openend else user_mode,
                **kwargs
            )
            already_openend = True

    @staticmethod
    def _split_path(path):
        if "/" not in path:
            return None, path
        return path.rsplit("/", 1)


class Plotter(FileHandler):
    """File handler that can save matplotlib.figure objects to a file.

    This is a specialised file handler object that can just store
    matplotlib.figure objects. It cannot read from a file nor get the time
    coverage from one. This is designed for having a simple plot dataset as
    output.
    """

    def __init__(self, fig_args=None, **kwargs):
        """Initializes a Plotter file handler class.

        Args:
            fig_args: A dictionary of additional keyword arguments for the
                fig.savefig method.
        """
        # Call the base class initializer
        super().__init__(**kwargs)

        if fig_args is None:
            self.fig_args = {}
        else:
            self.fig_args = fig_args

    @expects_file_info(pos=2)
    def write(self, figure, filename, fig_args=None):
        """ Saves a matplotlib.figure object to a file.

        Args:
            figure: A matplotlib.figure object.
            filename: Path and name of the file as string or FileInfo object.
            fig_args: A dictionary of additional keyword arguments for the
                fig.savefig method. This updates the *fig_args* given during
                initialisation.
        """

        params = self.fig_args.copy()
        if fig_args is not None:
            params.update(**fig_args)

        return figure.savefig(filename.path, **params)


# class Numpy(handlers.FileHandler):
#     def __init__(self, **kwargs):
#         # Call the base class initializer
#         super().__init__(**kwargs)
#
#     def get_info(self, filename):
#         # Get info parameters from a file (time coverage, etc)
#         ...
#
#     def read(self, filename, fields=None):
#         """ Reads and parses files with numpy arrays and load them to a xarray.
#
#         See the base class for further documentation.
#         """
#         numpy_data = np.load(filename)
#         print(numpy_data.keys())
#         data = xarray.Dataset.from_dict(numpy_data)
#
#         return data
#
#     def write(self, filename, data):
#         """ Writes a xarray to a NetCDF file.
#
#         See the base class for further documentation.
#         """
#
#         # Data must be a xarray object!
#         data_dict = data.to_dict()
#         np.save(filename, data_dict)
#
#
# class Pickle(handlers.FileHandler):
#     def __init__(self, **kwargs):
#         # Call the base class initializer
#         super().__init__(**kwargs)
#
#     def get_info(self, filename):
#         # Get info parameters from a file (time coverage, etc)
#         ...
#
#     def read(self, filename, fields=None):
#         """ Reads and parses files with numpy arrays and load them to a xarray.
#
#         See the base class for further documentation.
#         """
#
#         with open(filename, 'rb') as file:
#             return pickle.load(file)
#
#     def write(self, filename, data):
#         """ Writes a xarray to a NetCDF file.
#
#         See the base class for further documentation.
#         """
#
#         with open(filename, 'wb') as file:
#             pickle.dump(data, file)
#
#
# class XML(handlers.FileHandler):
#     def __init__(self, **kwargs):
#         # Call the base class initializer
#         super().__init__(**kwargs)
#
#     def get_info(self, filename):
#         # Get info parameters from a file (time coverage, etc)
#         ...
#
#     def read(self, filename, fields=None):
#         """ Reads and parses NetCDF files and load them to a xarray.
#
#         See the parent class for further documentation.
#         """
#         #
#         return typhon.arts.xml.load(filename)
#
#     def write(self, filename, data):
#         """ Writes a xarray to a NetCDF file.
#
#         See the base class for further documentation.
#         """
#
#         # Data must be a xarray object!
#         typhon.arts.xml.save(data, filename)