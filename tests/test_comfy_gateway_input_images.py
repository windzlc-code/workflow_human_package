import copy

from tools import comfy_gateway_v2


def test_input_image_roles_match_load_image_titles_before_order_fallback():
    prompt = {
        "1": {
            "class_type": "LoadImage",
            "_meta": {"title": "source face reference"},
            "inputs": {"image": "old_source.png"},
        },
        "2": {
            "class_type": "LoadImage",
            "_meta": {"title": "target original image"},
            "inputs": {"image": "old_target.png"},
        },
    }

    result = comfy_gateway_v2._apply_prompt_overrides(
        copy.deepcopy(prompt),
        {
            "input_images": [
                {"role": "target", "image": "target_uploaded.png"},
                {"role": "source_face", "image": "face_uploaded.png"},
            ]
        },
    )

    assert result["1"]["inputs"]["image"] == "face_uploaded.png"
    assert result["2"]["inputs"]["image"] == "target_uploaded.png"


def test_input_image_bindings_can_pin_roles_to_node_ids():
    prompt = {
        "10": {"class_type": "LoadImage", "_meta": {"title": "image a"}, "inputs": {"image": "a.png"}},
        "20": {"class_type": "LoadImage", "_meta": {"title": "image b"}, "inputs": {"image": "b.png"}},
    }

    result = comfy_gateway_v2._apply_prompt_overrides(
        prompt,
        {
            "input_images": [
                {"role": "target", "image": "target_uploaded.png"},
                {"role": "source_face", "image": "face_uploaded.png"},
            ],
            "input_image_bindings": {
                "target": {"node_id": "20"},
                "source_face": {"node_id": "10"},
            },
        },
    )

    assert result["10"]["inputs"]["image"] == "face_uploaded.png"
    assert result["20"]["inputs"]["image"] == "target_uploaded.png"


def test_firered_two_image_roles_can_pin_to_load_image_nodes():
    prompt = {
        "2": {"class_type": "LoadImage", "_meta": {"title": "image 1"}, "inputs": {"image": "old_1.jpg"}},
        "19": {"class_type": "LoadImage", "_meta": {"title": "image 2"}, "inputs": {"image": "old_2.jpg"}},
    }

    result = comfy_gateway_v2._apply_prompt_overrides(
        prompt,
        {
            "input_images": [
                {"role": "image1", "image": "original_uploaded.jpg"},
                {"role": "image2", "image": "reference_uploaded.jpg"},
            ],
            "input_image_bindings": {
                "image1": {"node_id": "2"},
                "image2": {"node_id": "19"},
            },
        },
    )

    assert result["2"]["inputs"]["image"] == "original_uploaded.jpg"
    assert result["19"]["inputs"]["image"] == "reference_uploaded.jpg"
