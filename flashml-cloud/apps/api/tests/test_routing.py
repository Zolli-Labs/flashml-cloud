import pytest
from flashml_cloud_api import routing
from flashml_cloud_api.routing import GpuRoutingUnavailable, job_capability_class


def test_no_resources_is_the_small_cpu_class():
    assert job_capability_class(None) == "cpu-small"
    assert job_capability_class({}) == "cpu-small"


def test_the_cpu_split_mirrors_the_marketplace_threshold():
    from flashml_cloud_api.marketplace import CPU_LARGE_MIN_CORES
    assert job_capability_class({"cpus": CPU_LARGE_MIN_CORES}) == "cpu-large"
    assert job_capability_class({"cpus": CPU_LARGE_MIN_CORES - 1}) == "cpu-small"


def test_gpu_jobs_are_refused_with_the_pin_gap_named():
    with pytest.raises(GpuRoutingUnavailable, match="gpuPerTask"):
        job_capability_class({"gpus": 1})


def test_the_result_is_always_a_ladder_class():
    from flashml_cloud_api.marketplace import CAPABILITY_CLASSES
    assert job_capability_class({"cpus": 2}) in CAPABILITY_CLASSES
