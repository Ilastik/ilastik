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
from functools import partial
import numpy
import vigra
from lazyflow.graph import Operator, InputSlot, OutputSlot
from lazyflow.classifiers import TikTorchLazyflowClassifierFactory
from lazyflow.operators import OpMultiArraySlicer2, OpValueCache,  OpBlockedArrayCache, \
                               OpClassifierPredict, OpTrainClassifierBlocked
from lazyflow.operators.tiktorchClassifierOperators import OpTikTorchTrainClassifierBlocked, \
                                                           OpTikTorchClassifierPredict
from lazyflow.roi import getBlockBounds
from ilastik.utility.operatorSubView import OperatorSubView
from ilastik.utility import OpMultiLaneWrapper

from ilastik.applets.pixelClassification.opPixelClassification import OpLabelPipeline
from ilastik.applets.serverConfiguration.opServerConfig import DEFAULT_SERVER_CONFIG

import logging
logger = logging.getLogger(__name__)

BLOCKSHAPE = (1, 256, 256, 1) #(1, 188, 188, 1)
logger.warning(f'Using hardcoded blockshape {BLOCKSHAPE}')


class OpNNClassification(Operator):
    """
    Top-level operator for pixel classification
    """
    
    name = "OpNNClassification"
    category = "Top-level"

    # Graph inputs
    InputImages = InputSlot(level=1)
    NumClasses = InputSlot(optional=True)
    LabelInputs = InputSlot(optional=True, level=1)
    FreezePredictions = InputSlot(stype='bool', value=False, nonlane=True)
    ClassifierFactory = InputSlot(optional=True)
    ServerConfig = InputSlot(value=DEFAULT_SERVER_CONFIG)
    TiktorchConfig = InputSlot(optional=True)
    BinaryModel = InputSlot(optional=True)
    BinaryModelState = InputSlot(value=b'')
    BinaryOptimizerState = InputSlot(value=b'')
    ValidationImgMask = InputSlot(level=1, optional=True, allow_mask=True)

    Classifier = OutputSlot()
    PredictionProbabilities = OutputSlot(level=1)  # Classification predictions (via feature cache for interactive speed)
    PredictionProbabilityChannels = OutputSlot(level=2)  # Classification predictions, enumerated by channel
    CachedPredictionProbabilities = OutputSlot(level=1)
    LabelImages = OutputSlot(level=1)
    NonzeroLabelBlocks = OutputSlot(level=1)

    # Gui only (not part of the pipeline)
    Halo_Size = InputSlot(value=0)
    Batch_Size = InputSlot(value=1)

    LabelNames = OutputSlot()
    LabelColors = OutputSlot()
    PmapColors = OutputSlot()

    def setupOutputs(self):
        self.LabelNames.meta.dtype = object
        self.LabelNames.meta.shape = (1,)
        self.LabelColors.meta.dtype = object
        self.LabelColors.meta.shape = (1,)
        self.PmapColors.meta.dtype = object
        self.PmapColors.meta.shape = (1,)
        if not self.ClassifierFactory.ready() and \
                self.ServerConfig.ready() and self.TiktorchConfig.ready() and self.BinaryModel.ready():
            # todo: Deserialize sequences as tuple of ints, not as numpy.ndarray
            # (which is a weird, implicit default in SerialDictSlot)
            # also note: converting form numpy.int32, etc to python's int
            def make_good(bad):
                good = bad
                if isinstance(bad, dict):
                    good = {}
                    for key, bad_value in bad.items():
                        good[key] = make_good(bad_value)
                elif isinstance(bad, numpy.integer):
                    good = int(bad)
                elif isinstance(bad, numpy.ndarray):
                    good = tuple(make_good(v) for v in bad)
                return good
            tiktorch_config = make_good(self.TiktorchConfig.value)

            self.ClassifierFactory.setValue(TikTorchLazyflowClassifierFactory(tiktorch_config,
                                                                              self.BinaryModel.value,
                                                                              self.BinaryModelState.value,
                                                                              self.BinaryOptimizerState.value,
                                                                              server_config=self.ServerConfig.value))
            try:
                projectManager = self._parent._shell.projectManager
                applet = self._parent._applets[2]
                assert applet.name == 'NN Training'
                # restore labels  # todo: clean up this workaround for resetting the user label block shape
                top_group_name = applet.dataSerializers[0].topGroupName
                group_name = 'LabelSets'
                label_serial_block_slot = [s for s in applet.dataSerializers[0].serialSlots if s.name == group_name][0]
                label_serial_block_slot.deserialize(projectManager.currentProjectFile[top_group_name])
            except:
                logger.debug('Could not restore labels after setting TikTorchLazyflowClassifierFactory.')

    def __init__(self, *args, **kwargs):
        """
        Instantiate all internal operators and connect them together.
        """
        super(OpNNClassification, self).__init__(*args, **kwargs)
        
        # Default values for some input slots
        self.FreezePredictions.setValue(True)
        self.LabelNames.setValue([])
        self.LabelColors.setValue([])
        self.PmapColors.setValue([])

        # SPECIAL connection: the LabelInputs slot doesn't get it's data
        # from the InputImages slot, but it's shape must match.
        self.LabelInputs.connect(self.InputImages)
       
        self.opBlockShape = OpMultiLaneWrapper(OpBlockShape, parent=self)
        self.opBlockShape.RawImage.connect(self.InputImages)
        self.opBlockShape.ClassifierFactory.connect(self.ClassifierFactory)

        # Hook up Labeling Pipeline
        self.opLabelPipeline = OpMultiLaneWrapper(OpLabelPipeline, parent=self, broadcastingSlotNames=['DeleteLabel'])
        self.opLabelPipeline.RawImage.connect(self.InputImages)
        self.opLabelPipeline.LabelInput.connect(self.LabelInputs)
        self.opLabelPipeline.DeleteLabel.setValue(-1)
        self.LabelImages.connect(self.opLabelPipeline.Output)
        self.NonzeroLabelBlocks.connect(self.opLabelPipeline.nonzeroBlocks)
        self.opLabelPipeline.BlockShape.connect(self.opBlockShape.BlockShapeTrain)

        # TRAINING OPERATOR
        self.opTrain = OpTikTorchTrainClassifierBlocked(parent=self)
        self.opTrain.ClassifierFactory.connect(self.ClassifierFactory)
        self.opTrain.Labels.connect(self.opLabelPipeline.Output)
        self.opTrain.Images.connect(self.InputImages)
        self.opTrain.nonzeroLabelBlocks.connect(self.opLabelPipeline.nonzeroBlocks)

        # CLASSIFIER CACHE
        # This cache stores exactly one object: the classifier itself.
        self.classifier_cache = OpValueCache(parent=self)
        self.classifier_cache.name = "OpNetworkClassification.classifier_cache"
        self.classifier_cache.inputs["Input"].connect(self.opTrain.outputs['Classifier'])
        self.classifier_cache.inputs["fixAtCurrent"].connect(self.FreezePredictions)
        self.Classifier.connect(self.classifier_cache.Output)

        # Hook up the prediction pipeline inputs
        self.opPredictionPipeline = OpMultiLaneWrapper(OpPredictionPipeline, parent=self)
        self.opPredictionPipeline.RawImage.connect(self.InputImages)
        self.opPredictionPipeline.Classifier.connect(self.classifier_cache.Output)
        self.opPredictionPipeline.NumClasses.connect(self.NumClasses)
        self.opPredictionPipeline.FreezePredictions.connect(self.FreezePredictions)
        self.opPredictionPipeline.BlockShape.connect(self.opBlockShape.BlockShapeInference)

        self.PredictionProbabilities.connect(self.opPredictionPipeline.PredictionProbabilities)
        self.CachedPredictionProbabilities.connect(self.opPredictionPipeline.CachedPredictionProbabilities)
        self.PredictionProbabilityChannels.connect(self.opPredictionPipeline.PredictionProbabilityChannels)

        def _updateNumClasses(*args):
            """
            When the number of labels changes, we MUST make sure that the prediction image changes its shape (the number of channels).
            Since setupOutputs is not called for mere dirty notifications, but is called in response to setValue(),
            we use this function to call setValue().
            """
            numClasses = len(self.LabelNames.value)
            self.NumClasses.setValue(numClasses)
            self.opTrain.MaxLabel.setValue(numClasses)

        self.LabelNames.notifyDirty(_updateNumClasses)

        def inputResizeHandler(slot, oldsize, newsize):
            if (newsize == 0):
                self.LabelImages.resize(0)
                self.NonzeroLabelBlocks.resize(0)
                self.PredictionProbabilities.resize(0)
                self.CachedPredictionProbabilities.resize(0)
                
        self.InputImages.notifyResized(inputResizeHandler)

        # Debug assertions: Check to make sure the non-wrapped operators stayed that way.
        assert self.opTrain.Images.operator == self.opTrain

        def handleNewInputImage(multislot, index, *args):
            def handleInputReady(slot):
                self._checkConstraints(index)
                self.setupCaches(multislot.index(slot))
            multislot[index].notifyReady(handleInputReady)

        self.InputImages.notifyInserted(handleNewInputImage)

        # All input multi-slots should be kept in sync
        # Output multi-slots will auto-sync via the graph
        multiInputs = [s for s in list(self.inputs.values()) if s.level >= 1]
        for s1 in multiInputs:
            for s2 in multiInputs:
                if s1 != s2:
                    def insertSlot(a, b, position, finalsize):
                        a.insertSlot(position, finalsize)
                    s1.notifyInserted(partial(insertSlot, s2))
                    
                    def removeSlot(a, b, position, finalsize):
                        a.removeSlot(position, finalsize)
                    s1.notifyRemoved(partial(removeSlot, s2))

    def set_classifier(self, tiktorch_config : dict, model_file : bytes, model_state : bytes, optimizer_state : bytes):
        self.TiktorchConfig.disconnect()  # do not create TiktorchClassifierFactory with invalid intermediate settings
        self.ClassifierFactory.disconnect()
        self.FreezePredictions.setValue(False)
        self.BinaryModel.setValue(model_file)
        self.BinaryModelState.setValue(model_state)
        self.BinaryOptimizerState.setValue(optimizer_state)
        # now all non-server settings are up to date...
        self.TiktorchConfig.setValue(tiktorch_config)  # ...setupOutputs can initialize a tiktorchClassifierFactory

    def send_hparams(self, hparams):
        self.ClassifierFactory.meta.hparams = hparams
        def _send_hparams(slot):
            classifierFactory = self.ClassifierFactory[:].wait()[0]
            classifierFactory.send_hparams(hparams=self.ClassifierFactory.meta.hparams)
        if not self.ClassifierFactory.ready():
            self.ClassifierFactory.notifyReady(_send_hparams)
        else:
            classifierFactory = self.ClassifierFactory[:].wait()[0]
            classifierFactory.send_hparams(hparams)

    def setupCaches(self, imageIndex):
        numImages = len(self.InputImages)
        inputSlot = self.InputImages[imageIndex]

        self.LabelInputs.resize(numImages)

        # Special case: We have to set up the shape of our label *input* according to our image input shape
        shapeList = list(self.InputImages[imageIndex].meta.shape)
        try:
            channelIndex = self.InputImages[imageIndex].meta.axistags.index('c')
            shapeList[channelIndex] = 1
        except:
            pass
        self.LabelInputs[imageIndex].meta.shape = tuple(shapeList)
        self.LabelInputs[imageIndex].meta.axistags = inputSlot.meta.axistags

    def _checkConstraints(self, laneIndex):
        """
        Ensure that all input images have the same number of channels.
        """
        if not self.InputImages[laneIndex].ready():
            return

        thisLaneTaggedShape = self.InputImages[laneIndex].meta.getTaggedShape()

        # Find a different lane and use it for comparison
        validShape = thisLaneTaggedShape
        for i, slot in enumerate(self.InputImages):
            if slot.ready() and i != laneIndex:
                validShape = slot.meta.getTaggedShape()
                break

        if 't' in thisLaneTaggedShape:
            del thisLaneTaggedShape['t']
        if 't' in validShape:
            del validShape['t']

        if validShape['c'] != thisLaneTaggedShape['c']:
            raise DatasetConstraintError(
                 "Pixel Classification with CNNs",
                 "All input images must have the same number of channels.  "\
                 "Your new image has {} channel(s), but your other images have {} channel(s)."\
                 .format(thisLaneTaggedShape['c'], validShape['c']))
            
        if len(validShape) != len(thisLaneTaggedShape):
            raise DatasetConstraintError(
                 "Pixel Classification with CNNs",
                 "All input images must have the same dimensionality.  "\
                 "Your new image has {} dimensions (including channel), but your other images have {} dimensions."\
                .format(len(thisLaneTaggedShape), len(validShape)))

    def setInSlot(self, slot, subindex, roi, value):
        # Nothing to do here: All inputs that support __setitem__
        #   are directly connected to internal operators.
        pass

    def propagateDirty(self, slot, subindex, roi):
        # Nothing to do here: All outputs are directly connected to
        #  internal operators that handle their own dirty propagation.
        self.PredictionProbabilityChannels.setDirty(slice(None))

    def addLane(self, laneIndex):
        numLanes = len(self.InputImages)
        assert numLanes == laneIndex, f'Image lanes must be appended. {numLanes}, {laneIndex})'
        self.InputImages.resize(numLanes + 1)

    def removeLane(self, laneIndex, finalLength):
        self.InputImages.removeSlot(laneIndex, finalLength)

    def getLane(self, laneIndex):
        return OperatorSubView(self, laneIndex)

    def importLabels(self, laneIndex, slot):
        # Load the data into the cache
        new_max = self.getLane(laneIndex).opLabelPipeline.opLabelArray.ingestData(slot)

        # Add to the list of label names if there's a new max label
        old_names = self.LabelNames.value
        old_max = len(old_names)
        if new_max > old_max:
            new_names = old_names + ["Label {}".format(x) for x in range(old_max+1, new_max+1)]
            self.LabelNames.setValue(new_names)

            # Make some default colors, too
            # FIXME: take the colors from default16_new
            from volumina import colortables
            default_colors = colortables.default16_new
            
            label_colors = self.LabelColors.value
            pmap_colors = self.PmapColors.value
            
            self.LabelColors.setValue(label_colors + default_colors[old_max:new_max])
            self.PmapColors.setValue(pmap_colors + default_colors[old_max:new_max])

    def mergeLabels(self, from_label, into_label):
        for laneIndex in range(len(self.InputImages)):
            self.getLane(laneIndex).opLabelPipeline.opLabelArray.mergeLabels(from_label, into_label)

    def clearLabel(self, label_value):
        for laneIndex in range(len(self.InputImages)):
            self.getLane(laneIndex).opLabelPipeline.opLabelArray.clearLabel(label_value)

    def get_val_layer(self, parameters):
        img_shape = self.InputImages[0].meta.shape
        num_blocks = parameters['num_blocks']
        block_shape = BLOCKSHAPE
        val_roi = []
        
        #works only for 1, 2 or 4 
        for i in range(num_blocks):
            if i < 2:
                val_roi.append(getBlockBounds(img_shape, block_shape, [0, 0, block_shape[1] * i, 0]))
            else:
                val_roi.append(getBlockBounds(img_shape, block_shape, [0, block_shape[1], block_shape[1] * (i - 2), 0]))

        binarymask = numpy.zeros(img_shape, dtype='uint8')

        for shapes in val_roi:
            binarymask[:, shapes[0][1] : shapes[1][1], shapes[0][2] : shapes[1][2], :] = 1

        self.ValidationImgMask.meta.dtype = numpy.uint8
        self.ValidationImgMask.meta.axistags = vigra.defaultAxistags('zyxc')
        self.ValidationImgMask.setValue(binarymask)

        #ToDo pass val_roi to tiktorchlazyflowclassifier
            

class OpBlockShape(Operator):
    RawImage = InputSlot()
    ClassifierFactory = InputSlot()

    BlockShapeTrain = OutputSlot()
    BlockShapeInference = OutputSlot()

    def __init__(self, *args, **kwargs):
        super(OpBlockShape, self).__init__(*args, **kwargs)

    def setupOutputs(self):
        self.BlockShapeTrain.setValue(self.setup_train())
        self.BlockShapeInference.setValue(self.setup_inference())

    def setup_train(self):
        return self.setup_inference()
        # tagged_shape = self.RawImage.meta.getTaggedShape()
        # # labels are created for one channel (i.e. the label) and only in the
        # # current time slice, so we can set both c and t to 1
        # tagged_shape['c'] = 1
        # if 't' in tagged_shape:
        #     tagged_shape['t'] = 1
        #
        # # Aim for blocks that are roughly 20px
        # #block_shape = self.ClassifierFactory.value.determineBlockShape([tagged_shape['x'], tagged_shape['y']],
        # #                                                               train=True)
        # #return (1, *tuple(block_shape), 1)
        #
        # return BLOCKSHAPE

    def setup_inference(self):
        axisOrder = self.RawImage.meta.getAxisKeys()

        blockDims = { 't' : (1,1),
                      'z' : (1,1),
                      'y' : (BLOCKSHAPE[0], BLOCKSHAPE[1]),
                      'x' : (BLOCKSHAPE[0], BLOCKSHAPE[1]),
                      'c' : (100,100) }

        return tuple(blockDims[k][1] for k in axisOrder)

    def execute(self, slot, subindex, roi, result):
        pass

    def propagateDirty(self, slot, subindex, roi):
        self.BlockShapeTrain.setDirty()
        self.BlockShapeInference.setDirty()


class OpPredictionPipeline(Operator):
    RawImage = InputSlot()
    Classifier = InputSlot()
    NumClasses = InputSlot()
    FreezePredictions = InputSlot()
    BlockShape = InputSlot()

    PredictionProbabilities = OutputSlot()
    CachedPredictionProbabilities = OutputSlot()
    PredictionProbabilityChannels = OutputSlot(level=1)

    def __init__(self, *args, **kwargs):
        super(OpPredictionPipeline, self).__init__(*args, **kwargs)

        self.cacheless_predict = OpTikTorchClassifierPredict(parent=self)
        self.cacheless_predict.name = "OpClassifierPredict (Cacheless Path)"
        self.cacheless_predict.Classifier.connect(self.Classifier)
        self.cacheless_predict.Image.connect(self.RawImage) # <--- Not from cache
        self.cacheless_predict.LabelsCount.connect(self.NumClasses)

        self.predict = OpTikTorchClassifierPredict(parent=self)
        self.predict.name = "OpClassifierPredict"
        self.predict.Classifier.connect(self.Classifier)
        self.predict.Image.connect(self.RawImage)
        self.predict.LabelsCount.connect(self.NumClasses)
        self.PredictionProbabilities.connect(self.predict.PMaps)

        self.prediction_cache = OpBlockedArrayCache(parent=self)
        self.prediction_cache.name = "BlockedArrayCache"
        self.prediction_cache.inputs["fixAtCurrent"].connect(self.FreezePredictions)
        self.prediction_cache.BlockShape.connect(self.BlockShape)
        self.prediction_cache.inputs["Input"].connect(self.predict.PMaps)
        self.CachedPredictionProbabilities.connect(self.prediction_cache.Output)

        self.opPredictionSlicer = OpMultiArraySlicer2(parent=self)
        self.opPredictionSlicer.name = "opPredictionSlicer"
        self.opPredictionSlicer.Input.connect(self.prediction_cache.Output)
        self.opPredictionSlicer.AxisFlag.setValue('c')
        self.PredictionProbabilityChannels.connect(self.opPredictionSlicer.Slices)

    def setupOutputs(self):
        pass

    def execute(self, slot, subindex, roi, result):
        assert False, "Shouldn't get here.  Output is assigned a value in setupOutputs()"

    def propagateDirty(self, slot, subindex, roi):
        # Our output changes when the input changed shape, not when it becomes dirty.
        pass
