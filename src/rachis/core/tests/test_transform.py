import unittest
from typing import Union
from tempfile import TemporaryDirectory

from rachis import Artifact
from rachis.core.testing.format import (
    ThirdStepFormat, FourthStepFormat, FifthStepFormat, Cephalapod)


class TestTransitiveTransfomrers(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with TemporaryDirectory() as tempdir:
            cls.first_format = Artifact.import_data(
                type='FirstStep', view=tempdir
            )

        cls.int_sequence = Artifact.import_data(
            type='IntSequence1', view=[1, 2, 3]
        )

    def test_first_to_third(self):
        """
        Path exists and is upgraded.
        FirstStepFormat -> SecondStepFormat -> ThirdStepFormat
        """
        view = self.first_format.view(ThirdStepFormat)
        self.assertEqual(type(view), ThirdStepFormat)

    def test_first_to_fourth_fails(self):
        """
        Path exists but is not upgraded.
        FirstStepFormat -> SecondStepFormat -> ThirdStepFormat -None-> Fourth
        """
        with self.assertRaisesRegex(Exception, 'No transformation from'):
            self.first_format.view(FourthStepFormat)

    def test_union_transitivity(self):
        """
        Path exists between FirstStepFormat and ThirdStepFormat but not between
        FirsStepFormat and Cephalapod.
        """
        view = self.first_format.view(Union[Cephalapod, ThirdStepFormat])
        self.assertEqual(type(view), ThirdStepFormat)

    def test_int_sequence_dir_to_list(self):
        """
        Tests that unwrapping a format and then transforming does not require
        upgrading the path.
        """
        view = self.int_sequence.view(list)
        self.assertEqual(type(view), list)

    def test_lossy_transformer(self):
        """
        Tests that path with lossy steps still completes.
        First -> Second -> Third -lossy-> Fifth
        """
        view = self.first_format.view(FifthStepFormat)
        self.assertEqual(type(view), FifthStepFormat)
