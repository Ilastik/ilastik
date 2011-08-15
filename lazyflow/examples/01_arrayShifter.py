"""
This operator shifts the data(e.g an image) of an Input Slot in one dimension. 
To make this operator work one has to connect the Input Slot with an Output Slot
of another operator, e.g. vimageReader. When all Input Slots of an operator are
connected, the notifyConnectAll method is called implicit. Here one can do different
checkings and define the type, shape and axistags of the Output Slot of the operator.
The calculation, here the shifting, is done in the getOutSlot method of the operator.
This method again is called in an implicit way (see below)
"""


import vigra
import threading
from lazyflow.graph import *
import copy

from lazyflow.operators.operators import OpArrayPiper 
from lazyflow.operators.vigraOperators import *
from lazyflow.operators.valueProviders import *
from lazyflow.operators.classifierOperators import *
from lazyflow.operators.generic import *



class OpArrayShifter1(Operator):
    name = "OpArrayShifter1"
    description = "simple shifting operator"
    #change value for another shift
    shift = 50   
    #create Input and Output Slots (objects) of the operator
    #the different InputSlots and OutputSlot are saved in the dictionaries
    #"inputs" and "output"
    inputSlots = [InputSlot("Input")]
    outputSlots = [OutputSlot("Output")]    
    
    #this method is called when all InputSlot, in this example only one,
    #are connected with an OutputSlot or a value is set.
    def notifyConnectAll(self):
        #new name for the InputSlot("Input")
        inputSlot = self.inputs["Input"]
        #define the type, shape and axistags of the Output-Slot
        self.outputs["Output"]._dtype = inputSlot.dtype
        self.outputs["Output"]._shape = inputSlot.shape
        self.outputs["Output"]._axistags = copy.copy(inputSlot.axistags)

    #this method calculates the shifting
    def getOutSlot(self, slot, key, result):
        
        #new name for the shape of the InputSlot
        shape =  self.inputs["Input"].shape     
        
        #get N-D coordinate out of slice
        ostart, ostop = sliceToRoi(key, shape)    
        #copy original array        
        rstart, rstop = ostart.copy(), ostop.copy()
      
        #shift the reading scope
        #change value '-2' for shifting another dimension
        rstart[-2] -=  self.shift
        rstop[-2]  -=  self.shift
        
        #calculate wrinting scope
        wstart = - numpy.minimum(rstart,rstart-rstart)
        wstop  = result.shape + numpy.minimum(numpy.array(shape)-rstop, rstop-rstop)
        
        #shifted rstart/rstop has to be in the original range (not out of range)
        #for shifts in both directions
        rstart = numpy.minimum(rstart,numpy.array(shape))
        rstart = numpy.maximum(rstart, rstart - rstart)
        rstop  = numpy.minimum(rstop,numpy.array(shape))
        rstop  = numpy.maximum(rstop, rstop-rstop)
        
        #create slice out of the reading start and stop coordinates                
        rkey = roiToSlice(rstart,rstop)       
        
        #create slice out of the reading start and stop coordinates                
        wkey = roiToSlice(wstart,wstop)
        
        #preallocate result array with 0's
        result[:] = 0
        
        #write the shifted scope to the output 
        #self.inputs["Input"][rkey] returns an "GetItemWriterObject" object
        #its method "writeInto" will be called, which will call the 
        #"fireRequest" method of the, in this case, the Input-Slot,
        #which will return an "GetItemRequestObject" object. While this
        #object will be creating the "putTask" method of the graph object 
        #will be called
        req = self.inputs["Input"][rkey].writeInto(result[wkey])
        res = req()
        return res

    def notifyDirty(self,slot,key):
        self.outputs["Output"].setDirty(key)

    @property
    def shape(self):
        return self.outputs["Output"]._shape
    
    @property
    def dtype(self):
        return self.outputs["Output"]._dtype


#create new Graphobject
g = Graph(numThreads = 1, softMaxMem = 2000*1024**2)

#create Image Reader       
vimageReader = OpImageReader(g)
#read an image 
vimageReader.inputs["Filename"].setValue("/net/gorgonzola/storage/cripp/lazyflow/tests/ostrich.jpg")

#create Shifter_Operator with Graph-Objekt as argument
shifter = OpArrayShifter1(g)

#connect Shifter-Input with Image Reader Output
#because the Operator has only one Input Slot in this example,
#the "notifyConnectAll" method is executed
shifter.inputs["Input"].connect(vimageReader.outputs["Image"])

#shifter.outputs["Output"][:]returns an "GetItemWriterObject" object.
#its method "allocate" will be executed, this method call the "writeInto"
#method which calls the "fireRequest" method of the, in this case, 
#"OutputSlot" object which calls another method in "OutputSlot and finally
#the "getOutSlot" method of our operator.
#The wait() function blocks other activities and waits till the results
# of the requested Slot are calculated and stored in the result area.
shifter.outputs["Output"][:].allocate().wait()

#create Image Writer
vimageWriter = OpImageWriter(g)
#set writing path
vimageWriter.inputs["Filename"].setValue("/net/gorgonzola/storage/cripp/lazyflow/lazyflow/examples/shift_result.jpg")
#connect Writer-Input with Shifter Operator-Output
vimageWriter.inputs["Image"].connect(shifter.outputs["Output"])

#write shifted image on disk
g.finalize()