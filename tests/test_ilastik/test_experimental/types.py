import enum
import dataclasses


@enum.unique
class TestData(str, enum.Enum):
    __test__ = False
    DATA_1_CHANNEL: str = ("Data_1channel.png", "yx", "yxc")
    DATA_3_CHANNEL: str = ("Data_3channel.png", "yxc", "yxc")
    DATA_1_CHANNEL_3D: str = ("Data_3D.npy", "zyxc", "zyxc")

    DATA_1_CHANNEL_SEG: str = ("Data_1channel_Segmentation.png", "yx", "yxc")
    DATA_1_CHANNEL_PROB: str = ("Data_1channel_Probabilities.png", "yx", "yxc")

    def __new__(cls, value, axes, headless_axes):
        obj = str.__new__(cls, value)
        obj._value_ = value
        obj.axes = axes
        obj.data_axes = headless_axes
        return obj


@enum.unique
class TestProjects(enum.Enum):
    __test__ = False
    PIXEL_CLASS_1_CHANNEL_XYC: str = "PixelClass.ilp"
    PIXEL_CLASS_1_CHANNEL_XY: str = "2464_PixelClassification_xy_input.ilp"
    PIXEL_CLASS_3_CHANNEL: str = "PixelClass3Channel.ilp"
    PIXEL_CLASS_3D: str = "PixelClass3D.ilp"
    PIXEL_CLASS_NO_CLASSIFIER: str = "PixelClassNoClassifier.ilp"
    PIXEL_CLASS_NO_DATA: str = "PixelClassNoData.ilp"
    PIXEL_CLASS_3D_2D_3D_FEATURE_MIX: str = "PixelClass3D_2D_3D_feature_mix.ilp"

    OBJ_CLASS_SEG_1_CHANNEL: str = "ObjectClassSeg.ilp"


@dataclasses.dataclass
class Dataset:
    path: str
    #: Axes to use for api prediction
    axes: str
    #: Axes to use for headless run
    data_axes: str

    def __str__(self):
        return path


class ApiTestDataLookup:
    def __init__(self, path_by_name):
        self._path_by_name = path_by_name
        self._fields = []

    def find_project(self, file_name: TestProjects) -> str:
        return self._path_by_name[file_name.value]

    def find_dataset(self, file_name: TestData) -> Dataset:
        path = self._path_by_name[file_name.value]
        return Dataset(path, file_name.axes, file_name.data_axes)
