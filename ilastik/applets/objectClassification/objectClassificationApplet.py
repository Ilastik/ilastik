from ilastik.applets.base.standardApplet import StandardApplet

from opObjectClassification import OpObjectClassification
from objectClassificationSerializer import ObjectClassificationSerializer

from guiMessage import OpGuiDialog, GuiDialog

class ObjectClassificationApplet(StandardApplet):
    def __init__(self,
                 name="Object Classification",
                 workflow=None,
                 projectFileGroupName="ObjectClassification"):
        self._topLevelOperator = OpObjectClassification(parent=workflow)
        self._trainDialog = OpGuiDialog(parent=workflow)
        super(ObjectClassificationApplet, self).__init__(name=name, workflow=workflow)

        self._serializableItems = [
            ObjectClassificationSerializer(projectFileGroupName,
                                           self.topLevelOperator)]


    @property
    def topLevelOperator(self):
        return self._topLevelOperator

    @property
    def dataSerializers(self):
        return self._serializableItems

    def createSingleLaneGui(self, imageLaneIndex):
        from objectClassificationGui import ObjectClassificationGui
        singleImageOperator = self.topLevelOperator.getLane(imageLaneIndex)
        gui = ObjectClassificationGui(singleImageOperator,
                                       self.shellRequestSignal,
                                       self.guiControlSignal)
        
        # setup message chain
        self._trainDialog.dialog = GuiDialog(gui)
        self._trainDialog.inputslot.connect(self._topLevelOperator.Warnings)
        
        return gui
