"""Golden hashes of every reference sample (timestamps removed). A change here means a sample's content changed: bump
SAMPLE_VERSION when that is intended, then update the hash. Refactors of the sample builder must leave these as they are."""
import hashlib
import json
import re

import pytest
from fixtures import full_design

from diagram_bot.samples import SAMPLES

GOLDEN = {
    "three-tier": "7312d7490463a8e45a54c368897ea2b7b211c26c4c992196471d7faf522733cd",
    "microservices": "45c0579dc878dbff45ebb5ca108b8d76238cb6bf450560caf7a7cc440bccca49",
    "nlb-alb": "de76343306b476910e695963b42a21f8f225eecb7694beef6e6ed9c07c06cd8b",
    "environments": "b78d150d09d6bf7efd129d7b1e6e43dbcaebcd3584cc43d7a0740c7ca44481d6",
    "hybrid": "8f2542248660264a846a3749448e9f6a77e13654a0ad545ab19b351749ab3b11",
    "eks-platform": "4d0503025752ec20c06443e2b24e666b6c12a37ecd597885ecc50e742aea8b4c",
    "data-platform": "1954e1b01f0a367d1714be40ff5a08cf61d65ab433aeaa6c293318471cad9e47",
    "ai-platform": "497bd88161284faa0d594ec34bf5f72b275a211f6bbac76070e6ab16a322a51d",
    "genai-rag": "d85af603e16da4837044934fde63cd07072a7f75b59475f57e3e8768c271cc65",
    "gpu-training": "599dfefd9b40146a8176574c9ecd539af71b87b3d3917921ded6c04301ebe7c9",
    "multi-account": "41964c86524a1078a8f40a5dcbf59cd95cf5c77b72f9b16d88e250bc95e80e19",
    "design": "64d5773b73450cae651de330f7f41b1a4677cf150be85b5e0e9e02a8db1a359e",
}


def digest(snapshot):
    text = re.sub(r"\d{4}-\d\d-\d\dT[0-9:.]+\+00:00", "", json.dumps(snapshot, sort_keys=True, ensure_ascii=False))
    return hashlib.sha256(text.encode()).hexdigest()


@pytest.mark.parametrize("key", SAMPLES)
def test_sample_content_is_unchanged(key):
    assert digest(SAMPLES[key]()) == GOLDEN[key]


def test_design_content_is_unchanged():
    assert digest(full_design().snapshot()) == GOLDEN["design"]
