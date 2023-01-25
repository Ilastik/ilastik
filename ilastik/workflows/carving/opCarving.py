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
# 		   http://ilastik.org/license.html
###############################################################################
# Python
from builtins import range
from enum import IntEnum, unique
import time
import numpy, h5py

# Lazyflow
from lazyflow.graph import Operator, InputSlot, OutputSlot
from lazyflow.stype import Opaque
from lazyflow.rtype import List
from lazyflow.roi import roiToSlice
from lazyflow.operators.opDenseLabelArray import OpDenseLabelArray
from lazyflow.operators.valueProviders import OpValueCache

# ilastik
from lazyflow.utility.timer import Timer
from ilastik.applets.base.applet import DatasetConstraintError

import logging

logger = logging.getLogger(__name__)

DEFAULT_LABEL_PREFIX = "Object "
DEFAULT_OBJECT_NAME = "<not saved yet>"


@unique
class Labels(IntEnum):
    """Label values for carving.
    Values must be same as in ilastiktools/carving.hxx#setSeeds.
    """

    BACKGROUND = 1
    FOREGROUND = 2


class OpCarving(Operator):
    name = "Carving"
    category = "interactive segmentation"

    # I n p u t s #

    # MST of preprocessed Graph
    MST = InputSlot()

    # These three slots are for display only.
    # All computation is done with the MST.
    OverlayData = InputSlot(
        optional=True
    )  # Display-only: Available to the GUI in case the input data was preprocessed in some way but you still want to see the 'raw' data.
    InputData = InputSlot()  # The data used by preprocessing (display only)
    FilteredInputData = InputSlot()  # The output of the preprocessing filter

    # write the seeds that the users draw into this slot
    WriteSeeds = InputSlot()

    # trigger an update by writing into this slot
    Trigger = InputSlot(value=numpy.zeros((1,), dtype=numpy.uint8))

    # number between 0.0 and 1.0
    # bias of the background
    # FIXME: correct name?
    BackgroundPriority = InputSlot(value=0.95)

    LabelNames = OutputSlot(stype="list")

    # a number between 0 and 256
    # below the number, no background bias will be applied to the edge weights
    NoBiasBelow = InputSlot(value=64)

    UncertaintyType = InputSlot()

    # O u t p u t s #

    # current object + background
    Segmentation = OutputSlot()

    Supervoxels = OutputSlot()

    Uncertainty = OutputSlot()

    # contains an array with the object labels done so far, one label for each
    # object
    DoneSegmentation = OutputSlot()

    CurrentObjectName = OutputSlot(stype="string")

    AllObjectNames = OutputSlot(rtype=List, stype=Opaque)

    HasSegmentation = OutputSlot(stype="bool")

    HintOverlay = OutputSlot()

    PmapOverlay = OutputSlot()

    MstOut = OutputSlot()

    #: User-defined prefix for autogenerated object names
    ObjectPrefix = OutputSlot(stype="string")

    def __init__(self, graph=None, hintOverlayFile=None, pmapOverlayFile=None, parent=None):
        super(OpCarving, self).__init__(graph=graph, parent=parent)
        self.opLabelArray = OpDenseLabelArray(parent=self)
        self.opLabelArray.MetaInput.connect(self.InputData)

        self._hintOverlayFile = hintOverlayFile
        self._mst = None
        self.has_seeds = False  # keeps track of whether or not there are seeds currently loaded, either drawn by the user or loaded from a saved object

        self.LabelNames.setValue(["Background", "Object"])

        # supervoxels of finished and saved objects
        self._done_seg_lut = None
        self._hints = None
        self._pmap = None
        if hintOverlayFile is not None:
            try:
                f = h5py.File(hintOverlayFile, "r")
            except Exception as e:
                logger.info("Could not open hint overlay '%s'" % hintOverlayFile)
                raise e
            self._hints = f["/hints"][numpy.newaxis, :, :, :, numpy.newaxis]

        if pmapOverlayFile is not None:
            try:
                f = h5py.File(pmapOverlayFile, "r")
            except Exception as e:
                raise RuntimeError("Could not open pmap overlay '%s'" % pmapOverlayFile)
            self._pmap = f["/data"][numpy.newaxis, :, :, :, numpy.newaxis]

        self._setCurrObjectName(DEFAULT_OBJECT_NAME)
        self.HasSegmentation.setValue(False)

        # keep track of a set of object names that have changed since
        # the last serialization of this object to disk
        self._dirtyObjects = set()
        self.preprocessingApplet = None

        self._opMstCache = OpValueCache(parent=self)
        self.MstOut.connect(self._opMstCache.Output)

        self.InputData.notifyReady(self._checkConstraints)
        self.ObjectPrefix.setValue(DEFAULT_LABEL_PREFIX)

    def _checkConstraints(self, *args):
        slot = self.InputData
        numChannels = slot.meta.getTaggedShape()["c"]
        if numChannels != 1:
            raise DatasetConstraintError(
                "Carving",
                "Input image must have exactly one channel.  "
                + "You attempted to add a dataset with {} channels".format(numChannels),
            )

        sh = slot.meta.shape
        ax = slot.meta.axistags
        if len(slot.meta.shape) != 5:
            # Raise a regular exception.  This error is for developers, not users.
            raise RuntimeError("was expecting a 5D dataset, got shape=%r" % (sh,))
        if slot.meta.getTaggedShape()["t"] != 1:
            raise DatasetConstraintError(
                "Carving",
                "Input image must not have more than one time slice.  "
                + "You attempted to add a dataset with {} time slices".format(slot.meta.getTaggedShape()["t"]),
            )

        for i in range(1, 4):
            if not ax[i].isSpatial():
                # This is for developers.  Don't need a user-friendly error.
                raise RuntimeError("%d-th axis %r is not spatial" % (i, ax[i]))

    def clearLabel(self, label_value):
        self.opLabelArray.DeleteLabel.setValue(label_value)
        if self._mst is not None:
            self._mst.clearSeed(label_value)
        self.opLabelArray.DeleteLabel.setValue(-1)

    def _clearLabels(self):
        # clear the labels
        self.opLabelArray.DeleteLabel.setValue(2)
        self.opLabelArray.DeleteLabel.setValue(1)
        self.opLabelArray.DeleteLabel.setValue(-1)
        if self._mst is not None:
            self._mst.clearSeeds()
        self.has_seeds = False

    def _setCurrObjectName(self, n):
        """
        Sets the current object name to n.
        """
        self._currObjectName = n
        self.CurrentObjectName.setValue(n)

    def _buildDone(self):
        """
        Builds the done segmentation anew, for example after saving an object or
        deleting an object.
        """
        if self._mst is None:
            return
        with Timer() as timer:
            self._done_seg_lut = numpy.zeros(self._mst.numNodes + 1, dtype=numpy.int32)
            logger.info("building 'done' lut")
            for name, objectSupervoxels in self._mst.object_lut.items():
                if name == self._currObjectName:
                    continue
                assert (
                    name in self._mst.object_names
                ), f"{name} not in self._mst.object_names, keys are {list(self._mst.object_names.keys())!r}"
                self._done_seg_lut[objectSupervoxels] = self._mst.object_names[name]
        logger.info("building the 'done' luts took {} seconds".format(timer.seconds()))

    def dataIsStorable(self):
        if self._mst is None:
            return False
        nodeSeeds = self._mst.gridSegmentor.getNodeSeeds()
        has_bg_seeds = numpy.any(nodeSeeds == Labels.BACKGROUND)
        has_fg_seeds = numpy.any(nodeSeeds == Labels.FOREGROUND)
        return has_bg_seeds and has_fg_seeds

    def setupOutputs(self):
        self.Segmentation.meta.assignFrom(self.InputData.meta)
        self.Segmentation.meta.dtype = numpy.uint32

        self.Supervoxels.meta.assignFrom(self.Segmentation.meta)
        self.DoneSegmentation.meta.assignFrom(self.Segmentation.meta)

        self.HintOverlay.meta.assignFrom(self.InputData.meta)
        self.PmapOverlay.meta.assignFrom(self.InputData.meta)

        self.Uncertainty.meta.assignFrom(self.InputData.meta)
        self.Uncertainty.meta.dtype = numpy.uint8

        self.Trigger.meta.shape = (1,)
        self.Trigger.meta.dtype = numpy.uint8

        if self._mst is not None:
            objects = list(self._mst.object_names.keys())
            self.AllObjectNames.meta.shape = (len(objects),)
        else:
            self.AllObjectNames.meta.shape = (0,)

        self.AllObjectNames.meta.dtype = object

    def getCurrentObjectName(self):
        f"""
        Returns current object name, which is {DEFAULT_OBJECT_NAME} until an object is loaded.
        """
        return self._currObjectName

    def doneObjectNamesForPosition(self, position3d):
        """
        Returns a list of names of objects which occupy a specific 3D position.
        List is empty if there are no objects present.
        """
        assert len(position3d) == 3

        # find the supervoxel that was clicked
        sv = self._mst.supervoxelUint32[position3d]
        names = []
        for name, objectSupervoxels in self._mst.object_lut.items():
            if numpy.sum(sv == objectSupervoxels) > 0:
                names.append(name)
        logger.info("click on %r, supervoxel=%d: %r" % (position3d, sv, names))
        return names

    @Operator.forbidParallelExecute
    def attachVoxelLabelsToObject(self, name, fgVoxels, bgVoxels):
        """
        Attaches Voxellabes to an object called name.
        """
        self._mst.object_seeds_fg_voxels[name] = fgVoxels
        self._mst.object_seeds_bg_voxels[name] = bgVoxels

    @Operator.forbidParallelExecute
    def clearCurrentLabeling(self, trigger_recompute=True):
        """
        Clears the current labeling.
        """
        self._clearLabels()
        self._mst.gridSegmentor.clearSeeds()

        self.Trigger.setDirty(slice(None))

    def restore_and_get_labels_for_object(self, name):
        """
        Loads a single object called name to be the currently edited object. Its
        not part of the done segmentation anymore.
        """
        assert self._mst is not None
        logger.info("[OpCarving] load object %s (opCarving=%d, mst=%d)" % (name, id(self), id(self._mst)))

        assert name in self._mst.object_lut
        assert name in self._mst.object_seeds_fg_voxels
        assert name in self._mst.object_seeds_bg_voxels
        assert name in self._mst.bg_priority
        assert name in self._mst.no_bias_below

        # set foreground and background seeds
        fgVoxelsSeedPos = self._mst.object_seeds_fg_voxels[name]
        bgVoxelsSeedPos = self._mst.object_seeds_bg_voxels[name]
        fgArraySeedPos = numpy.array(fgVoxelsSeedPos)
        bgArraySeedPos = numpy.array(bgVoxelsSeedPos)

        self._mst.setSeeds(fgArraySeedPos, bgArraySeedPos)

        # load the actual segmentation
        fgNodes = self._mst.object_lut[name]

        self._mst.setResulFgObj(fgNodes[0])

        self._setCurrObjectName(name)
        self.HasSegmentation.setValue(True)

        # now that 'name' is no longer part of the set of finished objects, rebuild the done overlay
        self._buildDone()
        return (fgVoxelsSeedPos, bgVoxelsSeedPos)

    def loadObject(self, name):
        logger.info(f"want to load object with name = {name}")
        if name not in self._mst.object_lut:
            logger.info("  --> no object with this name")
            return

        self.save_object(self._currObjectName)
        self._clearLabels()

        fgVoxels, bgVoxels = self.restore_and_get_labels_for_object(name)

        self.set_labels_into_WriteSeeds_input(fgVoxels, bgVoxels)

        # restore the correct parameter values
        mst = self._mst

        assert name in mst.object_lut
        assert name in mst.object_seeds_fg_voxels
        assert name in mst.object_seeds_bg_voxels
        assert name in mst.bg_priority
        assert name in mst.no_bias_below

        assert name in mst.bg_priority
        assert name in mst.no_bias_below

        self.BackgroundPriority.setValue(mst.bg_priority[name])
        self.NoBiasBelow.setValue(mst.no_bias_below[name])

        # The entire segmentation layer needs to be refreshed now.
        self.Segmentation.setDirty()

    def set_labels_into_WriteSeeds_input(self, fgVoxels, bgVoxels):
        fg_bounding_box_start = numpy.array(list(map(numpy.min, fgVoxels)))
        fg_bounding_box_stop = 1 + numpy.array(list(map(numpy.max, fgVoxels)))

        bg_bounding_box_start = numpy.array(list(map(numpy.min, bgVoxels)))
        bg_bounding_box_stop = 1 + numpy.array(list(map(numpy.max, bgVoxels)))

        bounding_box_start = numpy.minimum(fg_bounding_box_start, bg_bounding_box_start)
        bounding_box_stop = numpy.maximum(fg_bounding_box_stop, bg_bounding_box_stop)

        bounding_box_slicing = roiToSlice(bounding_box_start, bounding_box_stop)
        bounding_box_shape = tuple(bounding_box_stop - bounding_box_start)

        dtype = self.opLabelArray.Output.meta.dtype

        # Convert coordinates to be relative to bounding box
        fgVoxels = numpy.array(fgVoxels)
        fgVoxels = fgVoxels - numpy.array([bounding_box_start]).transpose()
        fgVoxels = list(fgVoxels)

        bgVoxels = numpy.array(bgVoxels)
        bgVoxels = bgVoxels - numpy.array([bounding_box_start]).transpose()
        bgVoxels = list(bgVoxels)

        with Timer() as timer:
            logger.info("Loading seeds....")
            z = numpy.zeros(bounding_box_shape, dtype=dtype)
            logger.info("Allocating seed array took {} seconds".format(timer.seconds()))
            z[fgVoxels] = Labels.FOREGROUND
            z[bgVoxels] = Labels.BACKGROUND
            self.WriteSeeds[(slice(0, 1),) + bounding_box_slicing + (slice(0, 1),)] = z[
                numpy.newaxis, :, :, :, numpy.newaxis
            ]
        logger.info("Loading seeds took a total of {} seconds".format(timer.seconds()))

    @Operator.forbidParallelExecute
    def deleteObject_impl(self, name):
        """
        Deletes an object called name.
        """

        del self._mst.object_lut[name]
        del self._mst.object_seeds_fg_voxels[name]
        del self._mst.object_seeds_bg_voxels[name]
        del self._mst.bg_priority[name]
        del self._mst.no_bias_below[name]

        # delete it from object_names, as it indicates
        # whether the object exists
        if name in self._mst.object_names:
            del self._mst.object_names[name]

        self._setCurrObjectName(DEFAULT_OBJECT_NAME)

        # now that 'name' has been deleted, rebuild the done overlay
        self._buildDone()

    def deleteObject(self, name):
        logger.info(f"want to delete object with name = {name}")
        if name not in self._mst.object_lut:
            logger.info("  --> no object with this name")
            return

        self.deleteObject_impl(name)
        self._clearLabels()
        # trigger a re-computation
        self.Trigger.setDirty(slice(None))
        self._dirtyObjects.add(name)

        objects = list(self._mst.object_names.keys())
        logger.info("save: len = {}".format(len(objects)))
        self.AllObjectNames.meta.shape = (len(objects),)

        self.HasSegmentation.setValue(False)

    @Operator.forbidParallelExecute
    def save_object(self, name):
        """
        Saves current object as name.
        """
        logger.info(f"   --> Saving object {name!r}")
        if name in self._mst.object_names:
            objNr = self._mst.object_names[name]
        else:
            # find free objNr
            if len(list(self._mst.object_names.values())) > 0:
                objNr = numpy.max(numpy.array(list(self._mst.object_names.values()))) + 1
            else:
                objNr = 1

        sVseg = self._mst.getSuperVoxelSeg()

        self._mst.object_names[name] = objNr

        self._mst.bg_priority[name] = self.BackgroundPriority.value
        self._mst.no_bias_below[name] = self.NoBiasBelow.value

        self._mst.object_lut[name] = numpy.where(sVseg == 2)

        self._setCurrObjectName(DEFAULT_OBJECT_NAME)
        self.HasSegmentation.setValue(False)

        objects = list(self._mst.object_names.keys())
        self.AllObjectNames.meta.shape = (len(objects),)

        # now that 'name' is no longer part of the set of finished objects, rebuild the done overlay
        self._buildDone()

    def get_label_voxels(self):
        # the voxel coordinates of fg and bg labels
        if not self.opLabelArray.NonzeroBlocks.ready():
            return None, None

        bg = [[], [], []]
        fg = [[], [], []]
        for slicing in self.opLabelArray.NonzeroBlocks[:].wait()[0]:
            label = self.opLabelArray.Output[slicing].wait()
            labels_bg = numpy.nonzero(label == Labels.BACKGROUND)
            labels_fg = numpy.nonzero(label == Labels.FOREGROUND)
            labels_bg = [labels_bg[d] + slicing[d].start for d in [1, 2, 3]]
            labels_fg = [labels_fg[d] + slicing[d].start for d in [1, 2, 3]]
            for i in range(3):
                bg[i].append(labels_bg[i])
                fg[i].append(labels_fg[i])

        for i in range(3):
            bg[i] = numpy.concatenate(bg[i], axis=0) if len(bg[i]) > 0 else numpy.array((), dtype=numpy.int32)
            fg[i] = numpy.concatenate(fg[i], axis=0) if len(fg[i]) > 0 else numpy.array((), dtype=numpy.int32)
        return fg, bg

    def saveObjectAs(self, name):
        self.save_object(name)

        fgVoxels, bgVoxels = self.get_label_voxels()

        self.attachVoxelLabelsToObject(name, fgVoxels=fgVoxels, bgVoxels=bgVoxels)

        self._clearLabels()

        # trigger a re-computation
        self.Trigger.setDirty(slice(None))

        self._dirtyObjects.add(name)

        self._mst.gridSegmentor.clearSeeds()

        self._mst.clearSegmentation()
        self.clearCurrentLabeling()

    def getMaxUncertaintyPos(self, label):
        # FIXME: currently working on
        uncertainties = self._mst.uncertainty.lut
        segmentation = self._mst.segmentation.lut
        uncertainty_fg = numpy.where(segmentation == label, uncertainties, 0)
        index_max_uncert = numpy.argmax(uncertainty_fg, axis=0)
        pos = self._mst.regionCenter[index_max_uncert, :]

        return pos

    def execute(self, slot, subindex, roi, result):
        self._mst = self.MST.value

        if slot == self.AllObjectNames:
            ret = list(self._mst.object_names.keys())
            return ret

        sl = roi.toSlice()
        if slot == self.Segmentation:
            # avoid data being copied
            temp = self._mst.getVoxelSegmentation(roi=roi)
            temp.shape = (1,) + temp.shape + (1,)

        elif slot == self.Supervoxels:
            # avoid data being copied
            temp = self._mst.supervoxelUint32[sl[1:4]]
            temp.shape = (1,) + temp.shape + (1,)
        elif slot == self.DoneSegmentation:
            # avoid data being copied
            if self._done_seg_lut is None:
                result[0, :, :, :, 0] = 0
                return result
            else:
                temp = self._done_seg_lut[self._mst.supervoxelUint32[sl[1:4]]]
                temp.shape = (1,) + temp.shape + (1,)
        elif slot == self.HintOverlay:
            if self._hints is None:
                result[:] = 0
                return result
            else:
                result[:] = self._hints[roi.toSlice()]
                return result
        elif slot == self.PmapOverlay:
            if self._pmap is None:
                result[:] = 0
                return result
            else:
                result[:] = self._pmap[roi.toSlice()]
                return result
        elif slot == self.Uncertainty:
            temp = self._mst.uncertainty[sl[1:4]]
            temp.shape = (1,) + temp.shape + (1,)
        else:
            raise RuntimeError("unknown slot")
        return temp  # avoid copying data

    def setInSlot(self, slot, subindex, roi, value):
        key = roi.toSlice()
        if slot == self.WriteSeeds:
            with Timer() as timer:
                logger.info("Writing seeds to label array")
                self.opLabelArray.LabelSinkInput[roi.toSlice()] = value
                logger.info("Writing seeds to label array took {} seconds".format(timer.seconds()))

            assert self._mst is not None

            # Important: mst.seeds will requires erased values to be 255 (a.k.a -1)
            with Timer() as timer:
                logger.info("Writing seeds to MST")
                self._mst.addSeeds(roi=roi, brushStroke=value.squeeze())
                logger.info(f"Writing seeds to MST took {timer.seconds()} seconds")

            self.has_seeds = True
        else:
            raise RuntimeError("unknown slots")

    def propagateDirty(self, slot, subindex, roi):
        if (
            slot == self.Trigger
            or slot == self.BackgroundPriority
            or slot == self.NoBiasBelow
            or slot == self.UncertaintyType
        ):
            if self._mst is None:
                return
            if not self.BackgroundPriority.ready():
                return
            if not self.NoBiasBelow.ready():
                return

            bgPrio = self.BackgroundPriority.value
            noBiasBelow = self.NoBiasBelow.value

            logger.info("compute new carving results with bg priority = %f, no bias below %d" % (bgPrio, noBiasBelow))
            t1 = time.perf_counter()
            labelCount = 2
            params = dict()
            params["prios"] = [1.0, bgPrio, 1.0]
            params["uncertainty"] = self.UncertaintyType.value
            params["noBiasBelow"] = noBiasBelow

            unaries = numpy.zeros((self._mst.numNodes + 1, labelCount + 1), dtype=numpy.float32)
            self._mst.run(unaries, **params)
            logger.info(" ... carving took %f sec." % (time.perf_counter() - t1))

            self.Segmentation.setDirty(slice(None))
            self.DoneSegmentation.setDirty(slice(None))
            hasSeg = numpy.any(self._mst.hasSeg)
            self.HasSegmentation.setValue(hasSeg)

        elif slot == self.MST:
            self._opMstCache.Input.disconnect()
            self._mst = self.MST.value
            self._opMstCache.Input.setValue(self._mst)

            if self.has_seeds:
                fgVoxels, bgVoxels = self.get_label_voxels()
                self.set_labels_into_WriteSeeds_input(fgVoxels, bgVoxels)
        elif (
            slot == self.OverlayData
            or slot == self.InputData
            or slot == self.FilteredInputData
            or slot == self.WriteSeeds
        ):
            pass
        else:
            assert False, "Unknown input slot: {}".format(slot.name)
