import numpy as np
import pytest
import vigra

from lazyflow.operators import OpRelabelConsecutive


@pytest.fixture
def oprelabel(graph):
    return OpRelabelConsecutive(graph=graph)


@pytest.fixture
def labels() -> vigra.VigraArray:
    return vigra.taggedView(2 * np.arange(0, 100, dtype=np.uint8).reshape((10, 10)), "yx")


@pytest.mark.parametrize("axistags", ["yx", "zyx", "xyz", "xztyc"])
def test_preserve_axistags(oprelabel, labels, axistags):
    labels = labels.withAxes(axistags)
    oprelabel.Input.setValue(labels)

    assert "".join(oprelabel.Output.meta.getAxisKeys()) == axistags


def test_simple(oprelabel, labels):
    oprelabel.Input.setValue(labels)
    relabeled = oprelabel.Output[:].wait()
    np.testing.assert_array_equal(relabeled, labels // 2)


def test_startlabel(oprelabel, labels):
    oprelabel.StartLabel.setValue(10)
    oprelabel.Input.setValue(labels)
    relabeled = oprelabel.Output[:].wait()

    # 0 is a special label (we assume it's background), so it is unchanged
    # the first found object will have value 10
    # with the input data being 0, 2, 4, 6, 8 ...: 0, 10, 11, 12, 13 ...
    expected = labels // 2
    expected[expected > 0] += 9
    np.testing.assert_array_equal(relabeled, expected)


@pytest.mark.parametrize(
    "dtype_in, expected_dtype",
    [
        ("uint8", "uint8"),
        ("uint16", "uint32"),  # uint16 will be converted to uint32 (vigra cannot handle uint16)
        ("uint32", "uint32"),
        ("uint64", "uint64"),
    ],
)
def test_input_datatypes(oprelabel, labels, dtype_in, expected_dtype):
    labels = labels.astype(dtype_in)
    oprelabel.Input.setValue(labels)
    relabeled = oprelabel.Output[:].wait()
    np.testing.assert_array_equal(relabeled, labels // 2)
    assert relabeled.dtype == expected_dtype


@pytest.mark.parametrize("dtype", ["float", "float16", "float32", "float64"])
def test_unsupported_dtype_raises(oprelabel, labels, dtype):
    labels = labels.astype(dtype)

    with pytest.raises(ValueError):
        oprelabel.Input.setValue(labels)
