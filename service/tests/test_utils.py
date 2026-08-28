"""Unit tests for service/utils.py: filename recovery + zero-padded ordering.

No app/env setup needed here since utils.py has no HTTP/subprocess/config
dependencies (see its module docstring), so we import it directly without
the service_env fixture.
"""
from __future__ import annotations

from service.utils import derive_filename, filename_from_content_disposition


class TestFilenameFromContentDisposition:
    def test_returns_none_for_missing_header(self):
        assert filename_from_content_disposition(None) is None

    def test_returns_none_for_empty_header(self):
        assert filename_from_content_disposition("") is None

    def test_extracts_plain_filename(self):
        assert (
            filename_from_content_disposition('attachment; filename="page-009.xml"')
            == "page-009.xml"
        )

    def test_extracts_filename_without_quotes(self):
        assert filename_from_content_disposition("attachment; filename=page-009.xml") == "page-009.xml"

    def test_extracts_rfc5987_star_filename(self):
        # filename*=UTF-8''... form, sometimes used for non-ASCII names.
        result = filename_from_content_disposition("attachment; filename*=UTF-8''p%C3%A4ge.xml")
        # The regex doesn't percent-decode; it just captures the raw token
        # after filename*=UTF-8''. Document actual (not ideal) behavior.
        assert result == "p%C3%A4ge.xml"

    def test_header_with_no_filename_directive_returns_none(self):
        assert filename_from_content_disposition("attachment") is None

    def test_header_with_empty_filename_value_returns_none(self):
        # filename="" -> the capture group requires >=1 non-quote char, so
        # the regex simply fails to match here -> None.
        assert filename_from_content_disposition('attachment; filename=""') is None


class TestDeriveFilename:
    def test_uses_content_disposition_filename_when_present(self):
        result = derive_filename(1, "file-id-123", 'attachment; filename="original.xml"')
        assert result == "0001_original.xml"

    def test_falls_back_to_file_id_when_no_header(self):
        result = derive_filename(1, "file-id-123", None)
        assert result == "0001_file-id-123.xml"

    def test_falls_back_to_file_id_when_header_has_no_filename(self):
        result = derive_filename(3, "abc-def", "attachment")
        assert result == "0003_abc-def.xml"

    def test_appends_xml_extension_if_missing(self):
        result = derive_filename(1, "file-id", 'attachment; filename="page1"')
        assert result == "0001_page1.xml"

    def test_does_not_double_extension_if_already_present(self):
        result = derive_filename(1, "file-id", 'attachment; filename="page1.xml"')
        assert result == "0001_page1.xml"

    def test_extension_check_is_case_insensitive(self):
        result = derive_filename(1, "file-id", 'attachment; filename="PAGE1.XML"')
        assert result == "0001_PAGE1.XML"

    def test_strips_directory_components_from_recovered_filename(self):
        # Path(original).name strips any path segments a hostile/broken
        # Content-Disposition header might smuggle in.
        result = derive_filename(1, "file-id", 'attachment; filename="../../etc/passwd.xml"')
        assert result == "0001_passwd.xml"

    def test_index_is_zero_padded_to_four_digits(self):
        assert derive_filename(1, "id", None).startswith("0001_")
        assert derive_filename(9, "id", None).startswith("0009_")
        assert derive_filename(10, "id", None).startswith("0010_")
        assert derive_filename(999, "id", None).startswith("0999_")

    def test_ten_or_more_files_sort_correctly_alphabetically(self):
        # Regression guard for the exact bug class this prefixing exists to
        # avoid: with naive non-zero-padded indices ("10_" vs "2_"),
        # alphabetical sort (which is what alto2anno.py's own
        # `sorted(os.listdir(...))` does) would put file 10 before file 2.
        names = [derive_filename(i, f"id-{i}", None) for i in range(1, 13)]
        assert sorted(names) == names, (
            "zero-padded filenames must already be in alphabetical order "
            "matching request/canvas order"
        )
        # Spot-check the specific pair the bug would get wrong.
        name_2 = derive_filename(2, "id-2", None)
        name_10 = derive_filename(10, "id-10", None)
        assert name_2 == "0002_id-2.xml"
        assert name_10 == "0010_id-10.xml"
        assert sorted([name_10, name_2]) == [name_2, name_10]
