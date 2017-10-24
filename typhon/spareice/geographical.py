# -*- coding: utf-8 -*-

"""General functions and classes for manipulating geographical data.
"""
import copy


import numpy as np
import typhon.plots

try:
    import xarray as xr
except ModuleNotFoundError:
    pass

__all__ = [
    'Array',
    'ArrayGroup',
    'GeoData',
]


class Array(np.ndarray):
    """An extended numpy array with attributes and dimensions.

    """

    def __new__(cls, data, attrs=None, dims=None):
        obj = np.asarray(data).view(cls)

        if attrs is not None:
            obj.attrs = attrs

        if dims is not None:
            obj.dims = dims

        return obj

    def __array_finalize__(self, obj):
        if obj is None:
            return

        self.attrs = getattr(obj, 'attrs', {})
        self.dims = getattr(
            obj, 'dims',
            ["dim_%d" % i for i in range(len(self.shape))]
        )

    @classmethod
    def concatenate(cls, objects, dim=None):
        """Concatenate multiple Array objects together.

        The returned Array object contains the attributes and dimension labels
        from the first object in the list.

        TODO:
            Maybe this could be implemented via __array_wrap__ or
            __array_ufunc__?

        Args:
            objects: List of GeoData objects to concatenate.
            dim:

        Returns:
            An Array object.
        """
        concat_array = np.concatenate(objects, dim)
        array = Array(
            concat_array, objects[0].attrs, objects[0].dims
        )

        return array

    @classmethod
    def from_xarray(cls, xarray_object):
        return cls(xarray_object.data, xarray_object.attrs, xarray_object.dims)

    def to_string(self, pretty=True):
        """

        Args:
            pretty:

        Returns:
            The array formatted as string object.
        """
        items = []
        if self.shape[0] < 5:
            items = self[:self.shape[0]]
        else:
            items = ", ".join([str(self[0]), str(self[1]), ".. ", str(self[2]),
                     str(self[3])])
        return "[{}]".format(items)

    def to_xarray(self):
        return xr.DataArray(self, attrs=self.attrs, dims=self.dims)


class ArrayGroup:
    """A specialised dictionary for arrays.

    Still under development.
    """

    def __init__(self, name=None):
        """Initializes an ArrayGroup object.

        Args:
            name: Name of the ArrayGroup as string.
        """

        self.attrs = {}

        # All variables (including groups) will be saved into this dictionary:
        self._vars = {}
        # Only the names of the groups will be saved here. Their content is
        # in self._vars.
        self._groups = set()

        if name is None:
            self.name = "{}, {}:".format(type(self), id(self))
        else:
            self.name = name

    def __contains__(self, item):
        var, rest = self.parse(item)
        if var == "/":
            return True

        if var in self._vars:
            if rest:
                return rest in self[var]
            return True

        return False

    def __iter__(self):
        self._iter_vars = self.vars(deep=True)
        return self

    def __next__(self):
        return next(self._iter_vars)

    def __delitem__(self, key):
        var, rest = self.parse(key)
        # The user tries to delete all variables:
        if not var:
            raise KeyError("The main group cannot be deleted. Use the clear "
                           "method to delete all variables and groups.")

        if not rest:
            del self._vars[var]

            if var in self._groups:
                self._groups.remove(var)
        else:
            del self._vars[var][rest]

    def __getitem__(self, item):
        """Enables dictionary-like access to the ArrayGroup.

        There are different ways to access one element of this ArrayGroup:

        * by *array_group["var"]*: returns a variable (Array) or group
            (ArrayGroup) object.
        * by *array_group["group/var"]*: returns the variable from the group
            object.
        * by *array_group["/"]*: returns the main group (dictionary of
            variables and groups).
        * by *array_group[0:10]*: returns a copy of the first ten elements
            for each variable in the ArrayGroup object. Note: all variables
            should have the same length.

        Args:
            item:

        Returns:

        """

        # Accessing via key:
        if isinstance(item, str):
            var, rest = self.parse(item)

            # All variables are requested:
            if not var:
                return self._vars

            if not rest:
                try:
                    return self._vars[var]
                except KeyError:
                    raise KeyError(
                        "There is neither a variable nor group named "
                        "'{}'!".format(var))
            else:
                if var in self._groups:
                    return self._vars[var][rest]
                else:
                    raise KeyError("'{}' is not a group!".format(var))

        # Selecting elements via slicing:
        elif isinstance(item, list) \
                or isinstance(item, int) \
                or isinstance(item, slice):
            return self.select(item)
        else:
            raise KeyError(
                "The must be a string, integer, list or slice object!")

    def __setitem__(self, key, value):
        var, rest = self.parse(key)

        if not var:
            raise ValueError("You cannot change the main group directly!")

        if not rest:
            # Automatic conversion from numpy array to Array.
            if not isinstance(value, ArrayGroup) \
                    and not isinstance(value, Array):
                value = Array(value)

            try:
                self._vars[var] = value
            except KeyError:
                raise KeyError(
                    "There is no variable or group named '{}'!".format(var))

            if isinstance(value, ArrayGroup):
                # Add this variable name to the group set.
                self._groups.add(var)
            elif var in self._groups:
                # Remove this variable name if it is not a group (GeoData)
                # object any longer.
                self._groups.remove(var)
        else:
            self._groups.add(var)
            self._vars[var] = type(self)()
            self._vars[var][rest] = value

    def __str__(self):
        info = "Name: {}\n".format(self.name)
        info += "Attributes:\n"
        for attr, value in self.attrs.items():
            info += "\t{} : {}\n".format(attr, value)
        else:
            info += "\t--\n"

        info += "Groups:\n"
        for group in self.groups(deep=True):
            info += "\t{}\n".format(group)
        else:
            info += "\t--\n"

        info += "Variables:\n"
        for var in self.vars(deep=True):
            info += "\t{} {}: {}\n".format(
                var, self[var].shape, self[var].to_string(pretty=True)
            )
        else:
            info += "\t--\n"

        return info

    def bin(self, bins, fields=None, deep=False):
        """Bins all arrays in this object.

        Args:
            bins: List of lists which contain the indices for the bins.
            fields: Filter
            deep: Bins also the arrays of the subgroups.

        Returns:
            An ArrayGroup object with binned arrays.
        """
        new_data = type(self)()
        for var, data in self.items(deep):
            if fields is not None and var not in fields:
                continue
            binned_data = np.asarray([
                data[indices]
                for i, indices in enumerate(bins)
            ])
            new_data[var] = binned_data
        return new_data

    def collapse(self, bins, collapser=None,
                 variation_filter=None, deep=False):
        """Fills bins for each variables and apply a function to them.

        Args:
            bins: List of lists which contain the indices for the bins.
            collapser: Function that should be applied on each bin (
                numpy.nanmean is the default).
            variation_filter: Bins which exceed a certain variation limit
                can be excluded. For doing this, you must set this parameter to
                a tuple/list of at least two elements: field name and
                variation threshold. A third element is optional: the
                variation function (the default is numpy.nanstd).
            deep: Collapses also the variables of the subgroups.

        Returns:
            An ArrayGroup object.
        """
        # Default collapser is the mean function:
        if collapser is None:
            collapser = np.nanmean

        # Exclude all bins where the inhomogeneity (variation) is too high
        passed = np.ones_like(bins).astype("bool")
        if isinstance(variation_filter, tuple):
            if len(variation_filter) >= 2:
                if len(self[variation_filter[0]].shape) > 1:
                    raise ValueError(
                        "The variation filter can only be used for "
                        "1-dimensional data! I.e. the field '{}' must be "
                        "1-dimensional!".format(variation_filter[0])
                    )

                # Bin only one field for testing of inhomogeneities:
                binned = self.bin(bins, fields=(variation_filter[0]))

                # The user can define a different variation function (
                # default is the standard deviation).
                if len(variation_filter) == 2:
                    variation = np.nanstd(binned[variation_filter[0]], 1)
                else:
                    variation = variation_filter[2](
                        binned[variation_filter[0]], 1)
                passed = variation < variation_filter[1]
            else:
                raise ValueError("The inhomogeneity filter must be a tuple "
                                 "of a field name, a threshold and (optional)"
                                 "a variation function.")

        bins = np.asarray(bins)

        # Before collapsing the data, we must bin it:
        collapsed_data = self.bin(bins[passed], deep=deep)

        # Collapse the data:
        for var, data in collapsed_data.items(deep):
            collapsed_data[var] = collapser(data, 1)
        return collapsed_data

    @classmethod
    def concatenate(cls, objects, dimension=None):
        """Concatenate multiple GeoData objects.

        Notes:
            The attribute and dimension information of some arrays may get
            lost.

        Args:
            objects: List of GeoData objects to concatenate.
            dimension: Dimension on which to concatenate.

        Returns:
            A
        """
        new_data = cls()
        for var in objects[0]:
            if isinstance(objects[0][var], cls):
                new_data[var] = cls.concatenate(
                    [obj[var] for obj in objects],
                    dimension)
            else:
                if dimension is None:
                    dimension = 0
                new_data[var] = Array.concatenate(
                    [obj[var] for obj in objects],
                    dimension)

        return new_data

    @classmethod
    def from_xarray(cls, xarray_object):
        """Creates a GeoData object from a xarray.Dataset object.

        Args:
            xarray_object: A xarray.Dataset object.

        Returns:
            GeoData object
        """

        array_dict = cls()
        for var in xarray_object:
            array_dict[var] = Array.from_xarray(xarray_object[var])

        array_dict.attrs.update(**xarray_object.attrs)

        return array_dict

    def get_range(self, field, deep=False):
        """Get the minimum and maximum of one field.

        Args:
            field: Name of the field.
            deep: Including also the fields in subgroups (not only main group).

        Returns:

        """
        start = []
        end = []
        for var in self.vars(deep, with_name=field):
            start.append(np.nanmin(self[var]))
            end.append(np.nanmax(self[var]))

        return min(start), max(end)

    def groups(self, deep=False):
        """Returns the names of all groups in this GeoData object.

        Args:
            deep: Including also subgroups (not only main group).

        Yields:
            Name of group.
        """

        for group in self._groups:
            yield group
            if deep:
                yield from (group + "/" + subgroup
                            for subgroup in self[group].groups(deep))

    def items(self, deep=False):
        """

        Args:
            deep: Including also subgroups (not only main group).

        Returns:

        """
        for var in self.vars(deep):
            yield var, self[var]

    @staticmethod
    def _level(var):
        level = len(var.split("/")) - 1
        if var.startswith("/"):
            level -= 1
        return level

    @classmethod
    def merge(cls, objects, groups=None, overwrite_error=True):
        """Merges multiple GeoData objects to one.

        Notes:
            Merging of sub groups with the same name does not work properly.

        Args:
            objects: List of GeoData objects.
            groups: List of strings. You can give each object in
                :param:`objects` a group. Must have the same length as
                :param:`objects`.
            overwrite_error: Throws a KeyError when trying to merge`
                ArrayGroups containing same keys.

        Returns:
            An ArrayGroup object.
        """
        inserted = set()
        merged_data = cls()
        for i, obj in enumerate(objects):
            for var in obj.vars(deep=True):
                if overwrite_error and var in inserted:
                    raise KeyError("The variable '{}' occurred multiple "
                                   "times!".format(var))
                else:
                    if groups is not None:
                        if groups[i] not in merged_data:
                            merged_data[groups[i]] = cls()
                        merged_data[groups[i]][var] = obj[var]
                    else:
                        merged_data[var] = obj[var]

        return merged_data

    @staticmethod
    def parse(key):
        """Parses *key* into group and field.

        You can access the groups and fields via different keys:

        * "value": Returns ("value", "")
        * "/value": Returns ("value", "")
        * "value1/value2/value3": Returns ("value1", "value2/value3")
        * "value/": Returns ("value", "")
        * "/": Returns ("", "")

        Args:
            key:

        Returns:

        """
        if key.startswith("/"):
            return key[1:], ""

        if "/" not in key:
            return key, ""

        var, rest = key.split("/", 1)
        return var, rest

    def plot(self, fields, plot_type="worldmap", fig=None, ax=None, **kwargs):
        """

        Args:
            plot_type:
            fields:
            fig:
            ax:
            **kwargs:

        Returns:

        """

        if plot_type == "worldmap":
            ax, scatter = typhon.plots.worldmap(
                self["lat"],
                self["lon"],
                self[fields[0]],
                fig, ax, **kwargs
            )
        else:
            raise ValueError("Unknown plot type: '{}'".format(plot_type))

        return ax

    def rename(self, mapping, inplace=True):
        if inplace:
            obj = self
        else:
            obj = copy.deepcopy(self)

        for old_name, new_name in mapping.items():
            array = obj[old_name]
            del obj[old_name]
            obj[new_name] = array

        return obj

    def select(self, indices):
        """Select an TODO.

        Args:
            indices:
            limit_to:

        Returns:

        """
        selected_data = ArrayGroup(self.name + "-selected")

        for var in self.vars(deep=True):
            selected_data[var] = self[var][indices]

        return selected_data

    def to_xarray(self):
        """Converts this ArrayGroup object to a xarray.Dataset.

        Returns:
            A xarray.Dataset object
        """

        xarray_object = xr.Dataset()
        for var, data in self.items(deep=True):
            xarray_object[var] = data.to_xarray()

        xarray_object.attrs.update(**self.attrs)

        return xarray_object

    def values(self, deep=False):
        for var in self.vars(deep):
            yield self[var]

    def vars(self, deep=False, with_name=None):
        """Returns the names of all variables in this GeoData object main
        group.

        Args:
            deep: Searching also in subgroups (not only main group).
            with_name: Only the variables with this name will be returned (
                makes only sense when *deep* is true).

        Yields:
            Full name of one variable (including group name).
        """

        # Only the variables of the main group:
        for var in self._vars:
            if with_name is not None and var != with_name:
                continue
            if var not in self._groups:
                yield var

        if deep:
            for group in self._groups:
                yield from (
                    group + "/" + sub_var
                    for sub_var in self[group].vars(deep, with_name)
                )


class GeoData(ArrayGroup):
    """A specialised ArrayGroup for geographical indexed data (with
    longitude, latitude and time field).

    Still under development. TODO.
    """

    def __len__(self):
        if "time" not in self:
            return 0

        return len(self["time"])

    def plot(self, fields, plot_type="worldmap", fig=None, ax=None, **kwargs):
        """

        Args:
            plot_type:
            fields:
            fig:
            ax:
            **kwargs:

        Returns:

        """

        if plot_type == "worldmap":
            ax, scatter = typhon.plots.worldmap(
                self["lat"],
                self["lon"],
                self[fields[0]],
                fig, ax, **kwargs
            )
        else:
            raise ValueError("Unknown plot type: '{}'".format(plot_type))

        return ax
