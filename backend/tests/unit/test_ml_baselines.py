import pytest

from app.ml.invoice_ocr import OCRUnavailableError, extract_ocr_text
from app.ml.invoice_text import extract_invoice_text
from app.ml.spend_anomaly import SpendObservation, detect_amount_outliers
from app.ml.spend_classifier import TfidfLogisticClassifier
from app.ml.synthetic_spend import generate_dataset
from app.ml.vendor_ranking import PairwiseRanker, VendorCandidate, ranking_metrics


def test_tfidf_logistic_classifier_trains_deterministically_on_labelled_text() -> None:
    rows = generate_dataset(examples_per_class=20, seed=5)
    model = TfidfLogisticClassifier(epochs=60).fit(
        [text for text, _ in rows], [label for _, label in rows], seed=5
    )

    first = model.predict_proba("server laptop hardware purchase")
    second = model.predict_proba("server laptop hardware purchase")
    assert model.predict("server laptop hardware purchase") == "hardware"
    assert first == second
    assert sum(first.values()) == pytest.approx(1.0)


def test_spend_classifier_rejects_invalid_training_data() -> None:
    with pytest.raises(ValueError, match="equally sized"):
        TfidfLogisticClassifier().fit(["laptop"], [])


def test_robust_amount_baseline_explains_only_high_outliers() -> None:
    rows = [SpendObservation(str(index), "software", float(100 + index % 3)) for index in range(10)]
    rows.append(SpendObservation("outlier", "software", 5000))

    signals = detect_amount_outliers(rows)

    assert [signal.transaction_id for signal in signals] == ["outlier"]
    assert "historical median" in signals[0].explanation
    assert "do not auto-block payment" in signals[0].recommended_action


def test_invoice_text_parser_normalizes_amounts_and_rejects_ambiguous_fields() -> None:
    fields = extract_invoice_text(
        "Vendor: Acme K.K.\nInvoice Number: INV-1\nSubtotal: ¥1,200.00\nSubtotal: ¥2,000.00\nTotal: ¥1,320"
    )

    assert fields["vendor"] == "Acme K.K."
    assert fields["invoice_number"] == "INV-1"
    assert fields["subtotal"] is None
    assert fields["total"] == "1320"


def test_ocr_adapter_validates_file_and_reports_missing_local_engine(monkeypatch) -> None:
    with pytest.raises(ValueError, match="does not match"):
        extract_ocr_text(b"not a png", "invoice.png")

    monkeypatch.setattr("app.ml.invoice_ocr.shutil.which", lambda _program: None)
    with pytest.raises(OCRUnavailableError, match="tesseract is unavailable"):
        extract_ocr_text(b"\x89PNG\r\n\x1a\nsynthetic", "invoice.png")


def test_pairwise_vendor_ranker_trains_on_preference_pairs() -> None:
    groups = [
        [
            VendorCandidate((1.0, 0.0, 0.5, 0.0, 0.0, 0.0, 0.0), 3, 1.0),
            VendorCandidate((0.0, 1.0, 0.5, 0.0, 0.0, 0.0, 0.0), 2, 0.5),
            VendorCandidate((0.0, 0.0, 0.5, 0.0, 0.0, 0.0, 0.0), 0, 0.1),
        ]
        for _ in range(3)
    ]
    ranker = PairwiseRanker(epochs=20).fit(groups)
    scores = [[ranker.score(candidate) for candidate in group] for group in groups]
    metrics = ranking_metrics(groups, scores, k=2)
    assert metrics["ndcg@2"] == 1.0
    assert metrics["precision@2"] == 1.0


def test_ocr_tolerant_parser_repairs_only_check_digit_confirmed_registration() -> None:
    from app.domain.qualified_invoice import check_digit, is_valid_registration_number

    assert check_digit("000012050002") == 7  # published NTA corporate number 7000012050002
    assert is_valid_registration_number("T7123456789012")
    assert not is_valid_registration_number("T1234567890123")
    text = (
        "Registration Number: 17123456789012\nPO Number: PO-2026-000123 :\nCurrency: JPY : :\nTotal: 1,2O0."
    )
    strict = extract_invoice_text(text)
    assert strict["registration_number"] == "17123456789012"
    assert strict["total"] is None
    tolerant = extract_invoice_text(text, ocr_tolerant=True)
    assert tolerant["registration_number"] == "T7123456789012"  # "1" read for "T", check digit agrees
    assert tolerant["purchase_order_number"] == "PO-2026-000123"
    assert tolerant["currency"] == "JPY"
    assert tolerant["total"] == "1200"
    # A repair the check digit cannot confirm is left empty rather than guessed.
    assert (
        extract_invoice_text("Registration Number: 11234567890123", ocr_tolerant=True)["registration_number"]
        is None
    )


def test_chunker_stops_at_end_and_overlaps() -> None:
    from app.modules.procurement.assistant import _chunks

    chunks = _chunks("あ" * 2000)
    assert [len(chunk) for chunk in chunks] == [900, 900, 440]  # previously ~120 extra tail fragments
    assert _chunks("short text") == ["short text"]


def test_japanese_text_is_tokenized_into_bigrams() -> None:
    from app.modules.procurement.assistant import ascii_tokens, tokenize

    assert ascii_tokens("登録番号") == []
    assert tokenize("登録番号 ＱＡ") == ["qa", "登録", "録番", "番号"]
