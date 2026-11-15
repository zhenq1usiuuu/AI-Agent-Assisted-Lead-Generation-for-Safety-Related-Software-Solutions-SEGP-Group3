from app.connectors import inspect_public_source


def test_powerbi_embed_is_reported_but_not_auto_fetched():
    result = inspect_public_source('<iframe src="https://app.powerbi.com/view?r=public"></iframe>',
        'https://dosh.gov.my/my/dashboard/', 'dosh_prosecution')
    assert result['targets'] == []
    assert result['external_embeds'][0]['provider'] == 'powerbi'


def test_login_page_cannot_be_promoted_to_certificate_records():
    result = inspect_public_source('<form><input type="password"></form><table><tr><td>Layout</td></tr></table>',
        'https://www.malaysiancertified.com.my/search.aspx', 'sirim_directory', 'Company')
    assert result['targets'] == [] and result['observed_table_count'] == 0
    assert any('login_required:' in reason for reason in result['limitations'])


def test_interactive_directory_is_explicitly_reported():
    result = inspect_public_source('<a href="#" data-onclick="onProductSearch">Product</a>',
        'https://www.malaysiancertified.com.my/index.jsp', 'sirim_directory')
    assert any('interactive_directory:' in reason for reason in result['limitations'])
