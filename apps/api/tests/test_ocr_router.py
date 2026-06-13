from app.providers.ocr_rapid import _is_page_scanned


class _FakePage:
    def __init__(self, image_count: int = 0):
        self._image_count = image_count

    def get_images(self):
        return [("xref",)] * self._image_count


NORMAL_TEXT = (
    "Invoice 1042 issued to Acme Corporation on January 5 2024 for services "
    "rendered including consulting and development work across three projects."
)


def test_empty_text_is_scanned():
    assert _is_page_scanned(_FakePage(), "") is True
    assert _is_page_scanned(_FakePage(), "   \n  ") is True


def test_gibberish_ocr_layer_is_scanned():
    # < 40% alphanumeric → hidden garbage layer from a scanner.
    assert _is_page_scanned(_FakePage(), "!!! @@@ ### $$$ %%% ^^^ &&&") is True


def test_image_dominant_short_text_is_scanned():
    assert _is_page_scanned(_FakePage(image_count=2), "short caption") is True


def test_native_text_page_is_not_scanned():
    assert _is_page_scanned(_FakePage(), NORMAL_TEXT) is False


def test_images_with_substantial_text_is_not_scanned():
    assert _is_page_scanned(_FakePage(image_count=2), NORMAL_TEXT) is False
