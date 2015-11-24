# -*- coding: utf-8 -*-

"""Testing the basic ARTS XML functions

This module provides basic functions to test the reading and writing
of ARTS XML files.
"""

from tempfile import mkstemp
import os

import numpy as np

from typhon.arts import xml


def _create_tensor(n):
    """Create a tensor of dimension n.

    Create a tensor with n dimensions with two entries in each dimension.
    The tensor is filled with increasing integers starting with 0.

    Args:
        n (int): number of dimensions

    Returns:
        np.ndarray: n-dimensional tensor

    """
    return np.arange(2 ** n).reshape(2 * np.ones(n).astype(int))


class TestLoad(object):
    """Testing the ARTS XML reading functions.

    This class provides functions to test the reading of XML files. For this
    purpose reference files are read and compared to the expexted results.

    Attributes:
        ref_dir (str): absolute path to the reference data directory.

    """
    ref_dir = os.path.join(os.path.dirname(__file__), "reference", "")

    def test_load_index(self):
        """Load reference XML file for ARTS type Index."""
        assert xml.load(self.ref_dir + 'index.xml') == 0


    def test_load_vector(self):
        """Load reference XML file for ARTS type Vector."""
        reference = _create_tensor(1)
        test_data = xml.load(self.ref_dir + 'vector.xml')
        assert np.array_equal(test_data, reference)


    def test_load_matrix(self):
        """Load reference XML file for ARTS type Matrix."""
        reference = _create_tensor(2)
        test_data = xml.load(self.ref_dir + 'matrix.xml')
        assert np.array_equal(test_data, reference)


    def test_load_tensor(self):
        """Load reference XML files for different Tensor types."""
        for n in range(3, 8):
            yield self._load_tensor, n


    def test_load_arrayofindex(self):
        """Load reference XML file for ARTS type ArrayOfIndex."""
        reference = [1., 2., 3.]
        test_data = xml.load(self.ref_dir + 'arrayofindex.xml')
        assert np.array_equal(test_data, reference)


    def test_load_arrayofstring(self):
        """Load reference XML file for ARTS type ArrayOfString."""
        reference = ['a', 'bb', 'ccc']
        test_data = xml.load(self.ref_dir + 'arrayofstring.xml')
        assert np.array_equal(test_data, reference)


    def _load_tensor(self, n):
        """Load tensor of dimension n and compare data to reference.

        Args:
            n (int): number of dimensions

        """
        reference = _create_tensor(n)
        test_data = xml.load(self.ref_dir + 'tensor{}.xml'.format(n))
        assert np.array_equal(test_data, reference)


class TestSave(object):
    """Testing the ARTS XML saving functions.

    This class provides functions to test the saving of XML files. Data is
    created and stored to a temporay file. Afterwards the file is read and
    the data gets compared to the initial data.

    Notes:
        The functions setUp() and tearDown() are run automatically before every
        other function.

    """
    def setUp(self):
        """Create a temporary file."""
        _, self.f = mkstemp()
        print(self.f)


    def tearDown(self):
        """Delete temporary file."""
        os.remove(self.f)


    def test_save_index(self):
        """Save Index to file, read it and compare the results."""
        reference = 0
        xml.save(reference, self.f)
        test_data = xml.load(self.f)
        assert test_data == reference


    def test_save_vector(self):
        """Save Vector to file, read it and compare the results."""
        reference = _create_tensor(1)
        xml.save(reference, self.f)
        test_data = xml.load(self.f)
        assert np.array_equal(test_data, reference)


    def test_save_matrix(self):
        """Save Matrix to file, read it and compare the results."""
        reference = _create_tensor(2)
        xml.save(reference, self.f)
        test_data = xml.load(self.f)
        assert np.array_equal(test_data, reference)


    def test_save_tensor(self):
        """Save different Tensor types to file, read and verify."""
        for n in range(3, 8):
            yield self._save_tensor, n


    def test_save_arrayofindex(self):
        """Save ArrayOfIndex to file, read it and compare the results."""
        reference = [1., 2., 3.]
        xml.save(reference, self.f)
        test_data = xml.load(self.f)
        assert np.array_equal(test_data, reference)


    def test_save_arrayofstring(self):
        """Save ArrayOfString to file, read it and compare the results."""
        reference = ['a', 'bb', 'ccc']
        xml.save(reference, self.f)
        test_data = xml.load(self.f)
        assert np.array_equal(test_data, reference)


    def _save_tensor(self, n):
        """Save tensor of dimension n to file, read it and compare data to
        reference.

        Args:
            n (int): number of dimensions

        """
        reference = _create_tensor(n)
        xml.save(reference, self.f)
        test_data = xml.load(self.f)
        assert np.array_equal(test_data, reference)
