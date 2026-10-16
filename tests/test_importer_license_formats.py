"""Publisher formatting variants retain the exact license and its restrictions."""
import hashlib
import json

import pytest

from medlab.importer import PMCClient, SourceRejected
from test_importer import cloud_session, response


def license_session(license_xml, code="CC BY"):
    session = cloud_session(code=code)
    replies = list(session.get.side_effect)
    raw = replies[2].iter_content.return_value[0]
    start, end = raw.index(b"<permissions>"), raw.index(b"</permissions>") + len(b"</permissions>")
    raw = raw[:start] + b"<permissions>" + license_xml.encode() + b"</permissions>" + raw[end:]
    metadata = json.loads(replies[1].iter_content.return_value[0])
    metadata["xml_url"] = "s3://pmc-oa-opendata/PMC123.2/PMC123.2.xml?md5=" + hashlib.md5(raw).hexdigest()
    session.get.side_effect = [replies[0], response(json.dumps(metadata).encode()), response(raw)]
    return session


def test_old_jats_escaped_closing_tag_is_removed_but_version_is_retained(tmp_path):
    xml = ('<license><license_ref>http://creativecommons.org/licenses/by/2.0&lt;/url&gt;),</license_ref>'
           '<license-p>This is an Open Access article under the Creative Commons Attribution License '
           '(&lt;url&gt;http://creativecommons.org/licenses/by/2.0&lt;/url&gt;), provided the original work is cited.</license-p></license>')
    result = PMCClient(tmp_path, session=license_session(xml)).verify("PMC123")
    assert result["provenance"]["license"]["url"] == "https://creativecommons.org/licenses/by/2.0/"
    assert "&lt;" not in result["provenance"]["license"]["text"]
    assert "</url>)," in result["provenance"]["license"]["text"]  # declaration preserved


def test_redundant_mixed_case_link_does_not_make_matching_nc_license_ambiguous(tmp_path):
    xml = ('<license href="http://creativecommons.org/licenses/by-nc/4.0/">'
           '<license_ref>https://creativecommons.org/licenses/by-nc/4.0/</license_ref>'
           '<license-p>Creative Commons Attribution NonCommercial 4.0 '
           '<uri href="http://creativecommons.org/Licenses/by-nc/4.0/">CC BY-NC</uri></license-p></license>')
    result = PMCClient(tmp_path, session=license_session(xml, "CC BY-NC"), reuse_scope="noncommercial").verify("PMC123")
    assert result["provenance"]["license"]["nonCommercial"] is True
    assert result["provenance"]["license"]["url"] == "https://creativecommons.org/licenses/by-nc/4.0/"


@pytest.mark.parametrize("data_text,accepted", [
    ('Data associated with the article are available under the terms of the Creative Commons Zero "No rights reserved" data waiver (CC0 1.0 Public domain dedication).', True),
    ('The article is also available under another license.', False),
])
def test_separate_article_by_and_explicit_data_cc0_nodes(tmp_path, data_text, accepted):
    xml = ('<license href="http://creativecommons.org/licenses/by/4.0/"><license-p>'
           'This is an open access article distributed under Creative Commons Attribution Licence.</license-p></license>'
           '<license href="http://creativecommons.org/publicdomain/zero/1.0/"><license-p>' + data_text + '</license-p></license>')
    client = PMCClient(tmp_path, session=license_session(xml))
    if accepted:
        result = client.verify("PMC123")
        assert result["provenance"]["license"]["code"] == "CC BY"
        assert data_text in result["provenance"]["license"]["text"]
    else:
        with pytest.raises(SourceRejected, match="mistura"):
            client.verify("PMC123")


@pytest.mark.parametrize("path", ["by-nd/2.0&lt;/url&gt;),", "by-nc-nd/4.0/", "by/2.0/unrecognized", "by/2.0&lt;/url&gt;)unexpected"])
def test_formatting_repair_never_strips_nd_or_arbitrary_trailing_terms(tmp_path, path):
    xml = '<license><license_ref>http://creativecommons.org/licenses/' + path + '</license_ref><license-p>Declared license.</license-p></license>'
    with pytest.raises(SourceRejected):
        PMCClient(tmp_path, session=license_session(xml), reuse_scope="noncommercial").verify("PMC123")
