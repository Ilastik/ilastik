#Python
import os

#SciPy
import numpy
import h5py

#lazyflow
from lazyflow.graph import Operator, InputSlot, OutputSlot
from lazyflow.roi import roiToSlice
from lazyflow.operators import OpBlockedArrayCache, OpMultiArraySlicer2
from lazyflow.operators import OpPixelFeaturesPresmoothed as OpPixelFeaturesPresmoothed_Original
from lazyflow.operators import OpPixelFeaturesInterpPresmoothed as OpPixelFeaturesPresmoothed_Interpolated
from lazyflow.operators.imgFilterOperators import OpPixelFeaturesPresmoothed as OpPixelFeaturesPresmoothed_Refactored

class OpFeatureSelectionNoCache(Operator):
    """
    The top-level operator for the feature selection applet for headless workflows.
    """
    name = "OpFeatureSelection"
    category = "Top-level"

    # Multiple input images
    InputImage = InputSlot()

    # The following input slots are applied uniformly to all input images
    Scales = InputSlot() # The list of possible scales to use when computing features
    FeatureIds = InputSlot() # The list of features to compute
    SelectionMatrix = InputSlot() # A matrix of bools indicating which features to output.
                         # The matrix rows correspond to feature types in the order specified by the FeatureIds input.
                         #  (See OpPixelFeaturesPresmoothed for the available feature types.)
                         # The matrix columns correspond to the scales provided in the Scales input,
                         #  which requires that the number of matrix columns must match len(Scales.value)
    FeatureListFilename = InputSlot(stype="str", optional=True)
    
    # Features are presented in the channels of the output image
    # Output can be optionally accessed via an internal cache.
    # (Training a classifier benefits from caching, but predicting with an existing classifier does not.)
    OutputImage = OutputSlot()

    FeatureLayers = OutputSlot(level=1) # For the GUI, we also provide each feature as a separate slot in this multislot

    # For ease of development and testing, the underlying feature computation implementation 
    #  can be switched via a constructor argument.  These are the possible choices.
    FilterImplementations = ['Original', 'Refactored', 'Interpolated']
    
    def __init__(self, filter_implementation, *args, **kwargs):
        super(OpFeatureSelectionNoCache, self).__init__(*args, **kwargs)

        # Create the operator that actually generates the features
        if filter_implementation == 'Original':
            self.opPixelFeatures = OpPixelFeaturesPresmoothed_Original(parent=self)
        elif filter_implementation == 'Refactored':
            self.opPixelFeatures = OpPixelFeaturesPresmoothed_Refactored(parent=self)
        elif filter_implementation == 'Interpolated':
            self.opPixelFeatures = OpPixelFeaturesPresmoothed_Interpolated(parent=self)
            self.opPixelFeatures.InterpolationScaleZ.setValue(2)
        else:
            raise RuntimeError("Unknown filter implementation option: {}".format( filter_implementation ))

        # Connect our internal operators to our external inputs 
        self.opPixelFeatures.Scales.connect( self.Scales )
        self.opPixelFeatures.FeatureIds.connect( self.FeatureIds )
        self.opPixelFeatures.Matrix.connect( self.SelectionMatrix )
        self.opPixelFeatures.Input.connect( self.InputImage )

    def setupOutputs(self):        
        if self.FeatureListFilename.ready() and len(self.FeatureListFilename.value) > 0:
            f = open(self.FeatureListFilename.value, 'r')
            self._files = []
            for line in f:
                line = line.strip()
                if len(line) > 0:
                    self._files.append(line)
            f.close()
            
            self.OutputImage.disconnect()
            self.FeatureLayers.disconnect()
            
            axistags = self.inputs["InputImage"].meta.axistags
            
            self.FeatureLayers.resize(len(self._files))
            for i in range(len(self._files)):
                f = h5py.File(self._files[i], 'r')
                shape = f["data"].shape
                assert len(shape) == 3
                dtype = f["data"].dtype
                f.close()
                self.FeatureLayers[i].meta.shape    = shape+(1,)
                self.FeatureLayers[i].meta.dtype    = dtype
                self.FeatureLayers[i].meta.axistags = axistags 
                self.FeatureLayers[i].meta.description = os.path.basename(self._files[i]) 
            
            self.OutputImage.meta.shape    = (shape) + (len(self._files),)
            self.OutputImage.meta.dtype    = dtype 
            self.OutputImage.meta.axistags = axistags 
            
            self.CachedOutputImage.meta.shape    = (shape) + (len(self._files),)
            self.CachedOutputImage.meta.axistags = axistags 
        else:
            # Connect our external outputs to our internal operators
            self.OutputImage.connect( self.opPixelFeatures.Output )
            self.FeatureLayers.connect( self.opPixelFeatures.Features )

    def propagateDirty(self, slot, subindex, roi):
        # Output slots are directly connected to internal operators
        pass
    
    def execute(self, slot, subindex, rroi, result):
        if len(self.FeatureListFilename.value) == 0:
            return
       
        assert result.dtype == numpy.float32
        
        key = roiToSlice(rroi.start, rroi.stop)
            
        if slot == self.FeatureLayers:
            index = subindex[0]
            f = h5py.File(self._files[index], 'r')
            result[...,0] = f["data"][key[0:3]]
            return result
        elif slot == self.OutputImage or slot == self.CachedOutputImage:
            assert result.ndim == 4
            assert result.shape[-1] == len(self._files), "result.shape = %r" % result.shape 
            assert rroi.start == 0, "rroi = %r" % (rroi,)
            assert rroi.stop  == len(self._files), "rroi = %r" % (rroi,)
            
            j = 0
            for i in range(key[3].start, key[3].stop):
                f = h5py.File(self._files[i], 'r')
                r = f["data"][key[0:3]]
                assert r.dtype == numpy.float32
                assert r.ndim == 3
                f.close()
                result[:,:,:,j] = r 
                j += 1
            return result  

class OpFeatureSelection( OpFeatureSelectionNoCache ):
    """
    This is the top-level operator of the feature selection applet when used in a GUI.
    It provides an extra output for cached data.
    """

    CachedOutputImage = OutputSlot()

    def __init__(self, *args, **kwargs):
        super( OpFeatureSelection, self).__init__( *args, **kwargs )

        # Create the cache
        self.opPixelFeatureCache = OpBlockedArrayCache(parent=self)
        self.opPixelFeatureCache.name = "opPixelFeatureCache"

        # Connect the cache to the feature output
        self.opPixelFeatureCache.Input.connect(self.opPixelFeatures.Output)
        self.opPixelFeatureCache.fixAtCurrent.setValue(False)

        # Connect external output to internal output
        self.CachedOutputImage.connect( self.opPixelFeatureCache.Output )

    def setupOutputs(self):
        super( OpFeatureSelection, self ).setupOutputs()

        if self.FeatureListFilename.ready() and len(self.FeatureListFilename.value) > 0:
            self.CachedOutputImage.disconnect()
            self.CachedOutputImage.meta.dtype = self.OutputImage.meta.dtype 
        
        else:
            blockDims = { 't' : (1,1),
                          'z' : (128,256),
                          'y' : (128,256),
                          'x' : (128,256),
                          'c' : (1000,1000) }
            
            axisOrder = [ tag.key for tag in self.InputImage.meta.axistags ]
            
            innerBlockShape = tuple( blockDims[k][0] for k in axisOrder )
            outerBlockShape = tuple( blockDims[k][1] for k in axisOrder )
    
            # Configure the cache        
            self.opPixelFeatureCache.innerBlockShape.setValue( innerBlockShape )
            self.opPixelFeatureCache.outerBlockShape.setValue( outerBlockShape )
