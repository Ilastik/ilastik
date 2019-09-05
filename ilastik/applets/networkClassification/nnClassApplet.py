###############################################################################
#   ilastik: interactive learning and segmentation toolkit
#
#       Copyright (C) 2011-2014, the ilastik developers
#                                <team@ilastik.org>
#
# This program is free software; you can redistribute it and/or
# modify it under the terms of the GNU General Public License
# as published by the Free Software Foundation; either version 2
# of the License, or (at your option) any later version.
#
# In addition, as a special exception, the copyright holders of
# ilastik give you permission to combine ilastik with applets,
# workflows and plugins which are not covered under the GNU
# General Public License.
#
# See the LICENSE file for details. License information is also available
# on the ilastik web site at:
#          http://ilastik.org/license.html
###############################################################################
from __future__ import absolute_import
from ilastik.applets.base.standardApplet import StandardApplet
from .opNNclass import OpNNClassification
from .nnClassSerializer import NNClassificationSerializer


class NNClassApplet(StandardApplet):
    """
    StandartApplet Subclass with SingleLangeGui and SingeLaneOperator
    """

    def __init__(self, workflow, projectFileGroupName):

        super(NNClassApplet, self).__init__("NN Classification", workflow=workflow)

        self._serializableItems = [
            NNClassificationSerializer(self.topLevelOperator, projectFileGroupName)
        ]  # Legacy (v0.5) importer
        self._gui = None
        self.predictionSerializer = self._serializableItems[0]

    @property
    def broadcastingSlots(self):
        """
        defines which variables will be shared with different lanes
        """
        return ["ModelPath", "FullModel", "FreezePredictions"]

    @property
    def dataSerializers(self):
        """
        A list of dataSerializer objects for loading/saving any project data the applet is responsible for
        """
        return self._serializableItems

    @property
    def singleLaneGuiClass(self):
        """
        This applet uses a single lane gui and shares variables through the broadcasting slots
        """
        from .nnClassGui import NNClassGui

        return NNClassGui

    @property
    def singleLaneOperatorClass(self):
        """
        Return the operator class which handles a single image.
        """
        return OpNNClassification
