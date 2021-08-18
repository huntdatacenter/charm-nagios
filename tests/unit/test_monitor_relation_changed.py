from unittest import mock


import monitors_relation_changed


class FakeHash:
    def __init__(self, value):
        self.value = value

    def hexdigest(self):
        return self.value


class TestComputeHostPrefixes:
    def test_short_case(self):
        """Test 7 character prefix (short case)."""
        self._run_test(
            [
                "01234567-89ab-cdef-0123-456789abcdef",
                "fedcba98-7654-3210-fedc-ba9876543210",
            ],
            [
                "0123456700000000000000000000000000000000000000000000000000000000",
                "fedcba9800000000000000000000000000000000000000000000000000000000",
            ],
            {
                "01234567-89ab-cdef-0123-456789abcdef": "0123456",
                "fedcba98-7654-3210-fedc-ba9876543210": "fedcba9",
            },
        )

    def test_mid_case(self):
        """Test 21 character prefix (mid-length case)."""
        self._run_test(
            [
                "01234567-89ab-cdef-0123-456789abcdef",
                "fedcba98-7654-3210-fedc-ba9876543210",
            ],
            [
                "0000000000000000000000000000000000000000000000000000000000000000",
                "0000000000000000000011111111111111111111111111111111111111111111",
            ],
            {
                "01234567-89ab-cdef-0123-456789abcdef": "000000000000000000000",
                "fedcba98-7654-3210-fedc-ba9876543210": "000000000000000000001",
            },
        )

    def test_worst_case(self):
        """Test 64 character "prefix" (worst case)."""
        self._run_test(
            [
                "01234567-89ab-cdef-0123-456789abcdef",
                "fedcba98-7654-3210-fedc-ba9876543210",
            ],
            [
                "0000000000000000000000000000000000000000000000000000000000000000",
                "0000000000000000000000000000000000000000000000000000000000000001",
            ],
            {
                "01234567-89ab-cdef-0123-456789abcdef": "0000000000000000000000000000000000000000000000000000000000000000",  # noqa: E501
                "fedcba98-7654-3210-fedc-ba9876543210": "0000000000000000000000000000000000000000000000000000000000000001",  # noqa: E501
            },
        )

    @mock.patch("hashlib.sha256")
    def _run_test(self, model_ids, fake_sha256sums, expected_result, hexdigest_mock):
        hexdigest_mock.side_effect = [FakeHash(value) for value in fake_sha256sums]
        prefixes = monitors_relation_changed.compute_host_prefixes(model_ids)
        assert prefixes == expected_result


def test_compute_fallback_host_prefix():
    prefix = monitors_relation_changed.compute_fallback_host_prefix(
        {
            "metadata": {
                "rid": "monitors:1",
                "unit": "remote-0123456789abcdef0123456789abcdef/1",
            }
        }
    )
    assert prefix == "monitors:1_1"
