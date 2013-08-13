from ilastik.applets.base.appletSerializer import AppletSerializer, SerialSlot

from lazyflow.operators.opInterpMissingData import OpDetectMissing

class FillMissingSlicesSerializer(AppletSerializer):


    ### reimplementation of methods ###
        
    def __init__(self, topGroupName, topLevelOperator):
        slots = [SerialSlot(topLevelOperator.PatchSize),SerialSlot(topLevelOperator.HaloSize)]
        super( FillMissingSlicesSerializer, self ).__init__(topGroupName, slots=slots)
        self._operator = topLevelOperator
    
    
    def _serializeToHdf5(self, topGroup, hdf5File, projectFilePath):
        dslot = self._operator.Detector[0]
        extractedSVM = dslot[:].wait()
        self._setDataset(topGroup, 'SVM', extractedSVM)
        for s in self._operator.innerOperators:
            s.resetDirty()
        
        
    def _deserializeFromHdf5(self, topGroup, groupVersion, hdf5File, projectFilePath):
        svm = self._operator.OverloadDetector.setValue(self._getDataset(topGroup, 'SVM'))
        for s in self._operator.innerOperators:
            s.resetDirty()
        

    def isDirty(self):
        return any([s.isDirty() for s in self._operator.innerOperators])
    
    
    ### internal ###

    def _setDataset(self, group, dataName, dataValue):
        if dataName not in group.keys():
            # Create and assign
            group.create_dataset(dataName, data=dataValue)
        else:
            # Assign (this will fail if the dtype doesn't match)
            group[dataName][()] = dataValue

    def _getDataset(self, group, dataName):
        try:
            result = group[dataName].value
        except KeyError:
            result = ''
        return result 
