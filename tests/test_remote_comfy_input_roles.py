from unittest.mock import patch

from webapp import server


def test_remote_comfy_upload_input_images_preserves_face_swap_roles(tmp_path):
    target = tmp_path / "target.jpg"
    source = tmp_path / "source.jpg"
    target.write_bytes(b"target")
    source.write_bytes(b"source")

    uploads = [
        {"image": "telegram/task/target.jpg"},
        {"image": "telegram/task/source.jpg"},
    ]

    with patch.object(server, "_remote_comfy_gateway_upload_image", side_effect=uploads) as upload:
        result = server._remote_comfy_upload_input_images(
            gateway_url="http://gateway",
            token="",
            task_id="task_1",
            payload={
                "target_image_local_path": str(target),
                "source_image_local_path": str(source),
            },
            task_type="face_swap",
        )

    assert upload.call_count == 2
    assert result == [
        {"role": "target", "image": "telegram/task/target.jpg", "label": "原图"},
        {"role": "source_face", "image": "telegram/task/source.jpg", "label": "人脸参考图"},
    ]


def test_remote_comfy_upload_input_images_preserves_image_edit_roles(tmp_path):
    input_image = tmp_path / "input.png"
    reference_image = tmp_path / "reference.png"
    input_image.write_bytes(b"input")
    reference_image.write_bytes(b"reference")

    uploads = [
        {"image": "telegram/task/input.png"},
        {"image": "telegram/task/reference.png"},
    ]

    with patch.object(server, "_remote_comfy_gateway_upload_image", side_effect=uploads) as upload:
        result = server._remote_comfy_upload_input_images(
            gateway_url="http://gateway",
            token="",
            task_id="task_2",
            payload={
                "input_image_local_path": str(input_image),
                "reference_image_local_path": str(reference_image),
            },
            task_type="get_nano_banana",
        )

    assert upload.call_count == 2
    assert result == [
        {"role": "image1", "image": "telegram/task/input.png", "label": "原图"},
        {"role": "image2", "image": "telegram/task/reference.png", "label": "参考图"},
    ]


def test_remote_comfy_upload_input_images_duplicates_single_image_edit_role(tmp_path):
    input_image = tmp_path / "input.png"
    input_image.write_bytes(b"input")

    uploads = [
        {"image": "telegram/task/input.png"},
        {"image": "telegram/task/input.png"},
    ]

    with patch.object(server, "_remote_comfy_gateway_upload_image", side_effect=uploads) as upload:
        result = server._remote_comfy_upload_input_images(
            gateway_url="http://gateway",
            token="",
            task_id="task_3",
            payload={"input_image_local_path": str(input_image)},
            task_type="single_image_edit",
        )

    assert upload.call_count == 2
    assert result == [
        {"role": "image1", "image": "telegram/task/input.png", "label": "原图"},
        {"role": "image2", "image": "telegram/task/input.png", "label": "原图"},
    ]


def test_remote_comfy_uploaded_bindings_become_node_inputs():
    result = server._remote_comfy_node_inputs_from_uploaded_image_bindings(
        [
            {"role": "image1", "image": "telegram/task/input.png"},
            {"role": "image2", "image": "telegram/task/reference.png"},
        ],
        {
            "image1": {"node_id": "2", "input_name": "image"},
            "image2": {"node_id": "19", "input_name": "image"},
        },
    )

    assert result == {
        "2": {"image": "telegram/task/input.png"},
        "19": {"image": "telegram/task/reference.png"},
    }


def test_remote_comfy_prefers_saved_output_over_preview_images(tmp_path):
    preview = tmp_path / "ComfyUI_temp_preview.png"
    final = tmp_path / "face_swap_00001_.png"
    preview.write_bytes(b"preview")
    final.write_bytes(b"final")

    result = {
        "local_outputs": [
            {"node": "268", "type": "temp", "local_path": str(preview)},
            {"node": "467", "type": "output", "local_path": str(final)},
        ]
    }

    assert server._first_remote_comfy_output_path(result) == str(final)
    assert server._remote_comfy_output_image_paths(result) == [str(final)]


def test_face_swap_seedvr_flag_switches_save_node_to_upscale_output():
    payload = {
        "remote_comfy_workflow_mappings": {
            "face_swap": {
                "path": "__converted__/flux_換臉工作流.api.json",
                "node_inputs": {
                    "467": {"images": ["251", 0], "filename_prefix": "telegram/face_swap"}
                },
            }
        },
        "face_swap_seedvr_upscale": True,
        "face_swap_random_seed": 789,
    }

    result = server._remote_comfy_node_inputs_from_payload(
        payload,
        task_type="face_swap",
        workflow_path="__converted__/flux_換臉工作流.api.json",
    )

    assert result["467"] == {
        "images": ["326", 0],
        "filename_prefix": "telegram/face_swap_seedvr",
    }
    assert result["256"] == {"noise_seed": 789}


def test_face_swap_random_seed_updates_noise_node_without_seedvr():
    payload = {
        "remote_comfy_workflow_mappings": {
            "face_swap": {
                "path": "__converted__/flux_換臉工作流.api.json",
                "node_inputs": {
                    "467": {"images": ["251", 0], "filename_prefix": "telegram/face_swap"}
                },
            }
        },
        "face_swap_random_seed": 123456,
    }

    result = server._remote_comfy_node_inputs_from_payload(
        payload,
        task_type="face_swap",
        workflow_path="__converted__/flux_換臉工作流.api.json",
    )

    assert result["256"] == {"noise_seed": 123456}
    assert result["467"] == {"images": ["251", 0], "filename_prefix": "telegram/face_swap"}


def test_face_swap_finished_markup_has_followup_actions():
    markup = server._send_telegram_reply_markup_for_finished_task("task_1", "face_swap")

    assert markup == {
        "inline_keyboard": [
            [{"text": "SeedVR 单独放大", "callback_data": "face_swap:seedvr:task_1"}],
            [
                {"text": "重新生成", "callback_data": "face_swap:rerun:task_1"},
                {"text": "返回菜单", "callback_data": "face_swap:main_menu"},
            ],
        ]
    }
