from __future__ import annotations

import json
from argparse import Namespace
from tempfile import TemporaryDirectory

import httpx
import pytest
import weixin_channel.cli as cli_module

from weixin_channel import (
    AccessPolicy,
    AccountSession,
    BotEvent,
    LoginEvent,
    MessageItemType,
    StateStore,
    SyncWeixinClient,
    RetryConfig,
    UploadedMedia,
    UploadMediaType,
    WeixinApi,
    WeixinClient,
    WeixinMessage,
    aes_ecb_padded_size,
    build_media_message_item,
    decrypt_aes_128_ecb,
    encrypt_aes_128_ecb,
    markdown_to_plain_text,
    strip_group_trigger,
)


def test_message_helpers() -> None:
    msg = WeixinMessage.model_validate(
        {
            "from_user_id": "u@im.wechat",
            "group_id": "g@chatroom",
            "message_type": 1,
            "context_token": "ctx",
            "item_list": [{"type": 1, "text_item": {"text": "hello"}}],
        }
    )
    assert msg.is_user_message
    assert msg.is_group_message
    assert msg.conversation_id == "g@chatroom"
    assert msg.text() == "hello"


def test_aes_roundtrip() -> None:
    key = b"0123456789abcdef"
    data = b"hello world"
    encrypted = encrypt_aes_128_ecb(data, key)
    assert len(encrypted) == aes_ecb_padded_size(len(data))
    assert decrypt_aes_128_ecb(encrypted, key) == data


def test_media_payload() -> None:
    uploaded = UploadedMedia(
        filekey="k",
        download_encrypted_query_param="param",
        aeskey_hex="00" * 16,
        file_size=3,
        file_size_ciphertext=16,
        media_type=UploadMediaType.FILE,
        file_name="a.txt",
    )
    item = build_media_message_item(uploaded)
    assert item["type"] == int(MessageItemType.FILE)
    assert item["file_item"]["file_name"] == "a.txt"
    assert item["file_item"]["media"]["encrypt_type"] == 1


def test_media_payload_with_thumbnail() -> None:
    uploaded = UploadedMedia(
        filekey="k",
        download_encrypted_query_param="param",
        aeskey_hex="00" * 16,
        file_size=3,
        file_size_ciphertext=16,
        media_type=UploadMediaType.IMAGE,
        thumb_download_encrypted_query_param="thumb",
        thumb_aeskey_hex="00" * 16,
        thumb_size=2,
        thumb_size_ciphertext=16,
    )
    item = build_media_message_item(uploaded)
    assert item["image_item"]["thumb_media"]["encrypt_query_param"] == "thumb"


def test_store_multi_account_and_seen_ids() -> None:
    with TemporaryDirectory() as temp:
        store = StateStore(temp)
        session = AccountSession.create(token="tok", account_id="a@im.bot")
        store.save_session(session)
        assert store.load_session().token == "tok"
        assert store.load_session("a@im.bot").token == "tok"
        assert store.list_account_ids() == ["a@im.bot"]
        store.save_seen_message_ids([1, 2, 3], "a@im.bot", limit=2)
        assert store.load_seen_message_ids("a@im.bot") == [2, 3]


def test_access_policy_group_trigger() -> None:
    msg = WeixinMessage.model_validate(
        {
            "from_user_id": "u",
            "group_id": "g",
            "message_type": 1,
            "item_list": [{"type": 1, "text_item": {"text": "hello"}}],
        }
    )
    assert not AccessPolicy(group_enabled=True, allow_groups={"g"}).allow(msg)
    assert AccessPolicy(group_enabled=True, allow_groups={"g"}, group_trigger_prefixes=("h",)).allow(msg)
    assert AccessPolicy(
        group_enabled=True,
        allow_groups={"g"},
        group_trigger_prefixes=(),
        group_trigger_keywords=("@bot",),
    ).allow(
        WeixinMessage.model_validate(
            {
                "from_user_id": "u",
                "group_id": "g",
                "message_type": 1,
                "item_list": [{"type": 1, "text_item": {"text": "@bot hello"}}],
            }
        )
    )
    assert strip_group_trigger("@bot hello", prefixes=(), keywords=("@bot",)) == "hello"


@pytest.mark.asyncio
async def test_cli_login_qrcode_prints_url_once(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    url = "https://example.test/qrcode"

    async def fake_login_events(**_kwargs: object):
        yield LoginEvent("qrcode", "Scan this QR code.", qrcode_url=url)

    def fake_terminal_qr_renderer(rendered_url: str) -> None:
        print(f"QR:{rendered_url}")

    monkeypatch.setattr(cli_module.WeixinClient, "login_events", fake_login_events)
    monkeypatch.setattr(cli_module, "terminal_qr_renderer", fake_terminal_qr_renderer)

    result = await cli_module.run_login(
        Namespace(
            state_dir=None,
            base_url="https://example.test",
            bot_type="3",
            timeout=1.0,
            max_refreshes=0,
            no_env_proxy=False,
            no_qr=False,
        )
    )

    out = capsys.readouterr().out
    assert result == 1
    assert out.count(url) == 1
    assert f"QR:{url}" in out


def test_markdown_to_plain_text() -> None:
    assert markdown_to_plain_text("**hello** [world](https://example.com)") == "hello world"


def test_public_event_and_sync_exports() -> None:
    event = BotEvent("x", "y")
    assert event.type == "x"
    assert SyncWeixinClient.__name__ == "SyncWeixinClient"


@pytest.mark.asyncio
async def test_api_headers_and_send_text_body() -> None:
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["headers"] = dict(request.headers)
        seen["body"] = json.loads(request.content.decode())
        return httpx.Response(200, json={})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as http:
        api = WeixinApi(base_url="https://example.test", token="tok", http_client=http)
        await api.send_text(to_user_id="u", text="hi", context_token="ctx", client_id="cid")

    assert seen["headers"]["authorizationtype"] == "ilink_bot_token"
    assert seen["headers"]["authorization"] == "Bearer tok"
    assert seen["headers"]["x-wechat-uin"]
    assert seen["body"]["base_info"]["channel_version"]
    assert seen["body"]["msg"]["context_token"] == "ctx"


@pytest.mark.asyncio
async def test_client_persistent_dedupe() -> None:
    responses = [
        {
            "ret": 0,
            "get_updates_buf": "c1",
            "msgs": [
                {
                    "message_id": 1,
                    "from_user_id": "u",
                    "message_type": 1,
                    "context_token": "ctx",
                    "item_list": [{"type": 1, "text_item": {"text": "a"}}],
                }
            ],
        },
        {
            "ret": 0,
            "get_updates_buf": "c2",
            "msgs": [
                {
                    "message_id": 1,
                    "from_user_id": "u",
                    "message_type": 1,
                    "context_token": "ctx",
                    "item_list": [{"type": 1, "text_item": {"text": "a"}}],
                }
            ],
        },
    ]

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=responses.pop(0) if responses else {"ret": 0, "msgs": []})

    with TemporaryDirectory() as temp:
        store = StateStore(temp)
        session = AccountSession.create(token="tok", account_id="a@im.bot")
        store.save_session(session)
        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as http:
            api = WeixinApi(base_url="https://example.test", token="tok", http_client=http)
            client = WeixinClient(
                session=session,
                store=store,
                api=api,
                retry=RetryConfig(seen_flush_interval=1),
            )
            gen = client.poll_messages(sleep_on_empty_s=0)
            first = await gen.__anext__()
            assert first.message_id == 1
            assert store.load_seen_message_ids("a@im.bot") == [1]
            # Second response repeats the same message id, so generator skips it.
            await gen.aclose()
