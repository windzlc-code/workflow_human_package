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


def test_ui_converter_exports_rgthree_power_lora_widgets():
    workflow = {
        "nodes": [
            {
                "id": 198,
                "type": "Power Lora Loader (rgthree)",
                "inputs": [
                    {"name": "model", "link": 1},
                    {"name": "clip", "link": 2},
                ],
                "widgets_values": [
                    {},
                    {"type": "PowerLoraLoaderHeaderWidget"},
                    {"on": True, "lora": "Character/person.safetensors", "strength": 0.8, "strengthTwo": 1.0},
                    {"on": False, "lora": "Shape/body.safetensors", "strength": 0.25, "strengthTwo": None},
                    {},
                    "",
                ],
            },
            {"id": 10, "type": "ModelProvider", "outputs": []},
            {"id": 11, "type": "ClipProvider", "outputs": []},
        ],
        "links": [
            [1, 10, 0, 198, 0, "MODEL"],
            [2, 11, 0, 198, 1, "CLIP"],
        ],
    }
    object_info = {
        "Power Lora Loader (rgthree)": {
            "input": {
                "required": {},
                "optional": {"model": ("MODEL",), "clip": ("CLIP",)},
            }
        }
    }

    prompt, warnings = comfy_gateway_v2._ui_workflow_to_api_prompt(workflow, object_info)

    assert prompt["198"]["inputs"]["model"] == ["10", 0]
    assert prompt["198"]["inputs"]["clip"] == ["11", 0]
    assert prompt["198"]["inputs"]["lora_1"]["lora"] == "Character/person.safetensors"
    assert prompt["198"]["inputs"]["lora_1"]["strength"] == 0.8
    assert prompt["198"]["inputs"]["lora_2"]["on"] is False
    assert not [item for item in warnings if "198:Power Lora Loader" in item]
