from __future__ import annotations

import json
from argparse import Namespace
from tempfile import TemporaryDirectory

import httpx
import pytest
import weixin_channel.cli as cli_module
import weixin_channel.client as client_module

from weixin_channel import (
    AccessPolicy,
    AccountSession,
    BotEvent,
    ConcurrencyConfig,
    IncomingMessage,
    LoginEvent,
    MediaConfig,
    MessageItem,
    MessageItemType,
    StateStore,
    SyncWeixinClient,
    RetryConfig,
    UploadedMedia,
    UploadMediaType,
    WeixinApi,
    WeixinApiError,
    WeixinBot,
    WeixinConfigError,
    WeixinClient,
    WeixinMessage,
    aes_ecb_padded_size,
    build_media_message_item,
    decrypt_aes_128_ecb,
    download_media_item,
    download_remote_file,
    encode_client_version,
    encrypt_aes_128_ecb,
    markdown_to_plain_text,
    resolve_cdn_download_url,
    strip_group_trigger,
    upload_media_file,
)
from weixin_channel.models import GetUploadUrlResponse


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


def test_encode_client_version() -> None:
    assert encode_client_version("2.1.8") == 131336
    assert encode_client_version("bad") == 0


def test_config_validation() -> None:
    with pytest.raises(WeixinConfigError):
        ConcurrencyConfig(mode="bad")
    with pytest.raises(WeixinConfigError):
        ConcurrencyConfig(max_concurrency=0)
    with pytest.raises(WeixinConfigError):
        MediaConfig(max_download_bytes=-1)


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


def test_resolve_cdn_download_url_prefers_full_url() -> None:
    assert (
        resolve_cdn_download_url(
            encrypted_query_param="fallback",
            full_url="https://cdn.example.test/full",
        )
        == "https://cdn.example.test/full"
    )
    assert resolve_cdn_download_url(encrypted_query_param="fallback").startswith(
        "https://novac2c.cdn.weixin.qq.com/c2c/download?"
    )


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
    seen: list[tuple[str, dict[str, str], dict | None]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content.decode()) if request.content else None
        seen.append((request.url.path, dict(request.headers), body))
        if request.url.path.endswith("/get_bot_qrcode"):
            return httpx.Response(200, json={"qrcode": "qr", "qrcode_img_content": "url"})
        return httpx.Response(200, json={})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as http:
        api = WeixinApi(base_url="https://example.test", token="tok", http_client=http)
        await api.get_bot_qrcode()
        await api.send_text(to_user_id="u", text="hi", context_token="ctx", client_id="cid")

    get_headers = seen[0][1]
    post_headers = seen[1][1]
    post_body = seen[1][2]
    assert get_headers["ilink-app-id"] == "bot"
    assert get_headers["ilink-app-clientversion"] == "131336"
    assert post_headers["ilink-app-id"] == "bot"
    assert post_headers["ilink-app-clientversion"] == "131336"
    assert post_headers["authorizationtype"] == "ilink_bot_token"
    assert post_headers["authorization"] == "Bearer tok"
    assert post_headers["x-wechat-uin"]
    assert post_body is not None
    assert post_body["base_info"]["channel_version"]
    assert post_body["msg"]["context_token"] == "ctx"


@pytest.mark.asyncio
async def test_login_events_handle_redirect(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_sleep(_seconds: float) -> None:
        return None

    requests: list[httpx.URL] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request.url)
        if request.url.path.endswith("/get_bot_qrcode"):
            return httpx.Response(200, json={"qrcode": "qr", "qrcode_img_content": "url"})
        if request.url.host == "example.test":
            return httpx.Response(
                200,
                json={"status": "scaned_but_redirect", "redirect_host": "redirect.example.test"},
            )
        return httpx.Response(
            200,
            json={
                "status": "confirmed",
                "bot_token": "tok",
                "ilink_bot_id": "bot-id",
                "ilink_user_id": "user-id",
                "baseurl": "https://redirect.example.test",
            },
        )

    monkeypatch.setattr(client_module.asyncio, "sleep", fake_sleep)
    with TemporaryDirectory() as temp:
        store = StateStore(temp)
        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as http:
            api = WeixinApi(base_url="https://example.test", http_client=http)
            monkeypatch.setattr(client_module, "WeixinApi", lambda **_kwargs: api)
            events = [
                event
                async for event in WeixinClient.login_events(
                    store=store,
                    base_url="https://example.test",
                    timeout_s=10,
                )
            ]
        saved = store.load_session()

    assert [event.type for event in events] == ["qrcode", "redirected", "confirmed", "connected"]
    assert any(url.host == "redirect.example.test" for url in requests)
    assert saved is not None
    assert saved.token == "tok"


@pytest.mark.asyncio
async def test_get_updates_api_error_raises() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ret": 123, "errmsg": "bad cursor"})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as http:
        api = WeixinApi(base_url="https://example.test", token="tok", http_client=http)
        with pytest.raises(WeixinApiError) as exc_info:
            await api.get_updates("cursor")

    assert exc_info.value.errcode == 123


@pytest.mark.asyncio
async def test_send_text_chunks_by_utf8_bytes() -> None:
    sent: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content.decode())
        sent.append(body["msg"]["item_list"][0]["text_item"]["text"])
        return httpx.Response(200, json={})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as http:
        api = WeixinApi(base_url="https://example.test", token="tok", http_client=http)
        client = WeixinClient(api=api)
        await client.send_text(to_user_id="u", text="你a好", context_token="ctx", chunk_limit=4)

    assert sent == ["你a", "好"]


@pytest.mark.asyncio
async def test_incoming_messages_split_items_and_reply_markdown() -> None:
    responses = [
        {
            "ret": 0,
            "get_updates_buf": "c1",
            "msgs": [
                {
                    "message_id": 10,
                    "from_user_id": "u",
                    "message_type": 1,
                    "context_token": "ctx",
                    "item_list": [
                        {"type": 1, "text_item": {"text": "hello"}},
                        {
                            "type": 4,
                            "file_item": {
                                "file_name": "a.txt",
                                "media": {"encrypt_query_param": "param"},
                            },
                        },
                    ],
                }
            ],
        }
    ]
    sent: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/getupdates"):
            return httpx.Response(200, json=responses.pop(0))
        sent.append(json.loads(request.content.decode()))
        return httpx.Response(200, json={})

    with TemporaryDirectory() as temp:
        store = StateStore(temp)
        session = AccountSession.create(token="tok", account_id="a@im.bot")
        store.save_session(session)
        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as http:
            api = WeixinApi(base_url="https://example.test", token="tok", http_client=http)
            client = WeixinClient(session=session, store=store, api=api)
            gen = client.incoming_messages(sleep_on_empty_s=0)
            first = await gen.__anext__()
            second = await gen.__anext__()
            await first.reply_markdown("**hi** [there](https://example.test)")
            await gen.aclose()

    assert isinstance(first, IncomingMessage)
    assert first.text == "hello"
    assert second.is_file
    assert second.file_name == "a.txt"
    assert sent[0]["msg"]["item_list"][0]["text_item"]["text"] == "hi there"


@pytest.mark.asyncio
async def test_bot_item_level_text_handler_replies() -> None:
    sent: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(json.loads(request.content.decode()))
        return httpx.Response(200, json={})

    raw = WeixinMessage.model_validate(
        {
            "message_id": 1,
            "from_user_id": "u",
            "message_type": 1,
            "context_token": "ctx",
            "item_list": [{"type": 1, "text_item": {"text": "hello"}}],
        }
    )
    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as http:
        api = WeixinApi(base_url="https://example.test", token="tok", http_client=http)
        client = WeixinClient(session=AccountSession.create(token="tok"), api=api)
        bot = WeixinBot(client, item_level=True)
        seen: list[str | None] = []

        @bot.on_text
        async def handle(msg):
            seen.append(msg.text)
            return f"echo: {msg.text}"

        incoming = IncomingMessage(client=client, raw_message=raw, item=raw.item_list[0])
        await bot._process_message(incoming)

    assert seen == ["hello"]
    assert sent[0]["msg"]["item_list"][0]["text_item"]["text"] == "echo: hello"


@pytest.mark.asyncio
async def test_upload_media_file_prefers_upload_full_url() -> None:
    class FakeApi:
        async def get_upload_url(self, **_kwargs: object) -> GetUploadUrlResponse:
            return GetUploadUrlResponse(upload_full_url="https://cdn.example.test/full-upload")

    requests: list[httpx.URL] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request.url)
        return httpx.Response(200, headers={"x-encrypted-param": "download-param"})

    with TemporaryDirectory() as temp:
        path = f"{temp}/a.txt"
        with open(path, "wb") as fh:
            fh.write(b"hello")
        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as http:
            uploaded = await upload_media_file(
                api=FakeApi(),  # type: ignore[arg-type]
                file_path=path,
                to_user_id="u",
                media_type=UploadMediaType.FILE,
                http_client=http,
            )

    assert str(requests[0]) == "https://cdn.example.test/full-upload"
    assert uploaded.download_encrypted_query_param == "download-param"


@pytest.mark.asyncio
async def test_download_media_item_prefers_full_url() -> None:
    requests: list[httpx.URL] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request.url)
        return httpx.Response(200, content=b"plain")

    item = MessageItem.model_validate(
        {
            "type": int(MessageItemType.FILE),
            "file_item": {
                "file_name": "a.txt",
                "media": {
                    "full_url": "https://cdn.example.test/full-download",
                    "encrypt_query_param": "fallback",
                },
            },
        }
    )
    with TemporaryDirectory() as temp:
        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as http:
            downloaded = await download_media_item(item, dest_dir=temp, http_client=http)

    assert str(requests[0]) == "https://cdn.example.test/full-download"
    assert downloaded is not None
    assert downloaded.file_name == "a.txt"


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


@pytest.mark.asyncio
async def test_client_seen_ids_keep_recent_order() -> None:
    responses = [
        {
            "ret": 0,
            "get_updates_buf": f"c{message_id}",
            "msgs": [
                {
                    "message_id": message_id,
                    "from_user_id": "u",
                    "message_type": 1,
                    "context_token": "ctx",
                    "item_list": [{"type": 1, "text_item": {"text": str(message_id)}}],
                }
            ],
        }
        for message_id in (5, 1, 3)
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
                retry=RetryConfig(seen_flush_interval=1, seen_cache_limit=2),
            )
            gen = client.poll_messages(sleep_on_empty_s=0)
            assert (await gen.__anext__()).message_id == 5
            assert (await gen.__anext__()).message_id == 1
            assert (await gen.__anext__()).message_id == 3
            await gen.aclose()

        assert store.load_seen_message_ids("a@im.bot") == [1, 3]


@pytest.mark.asyncio
async def test_download_remote_file_aborts_when_max_bytes_exceeded() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"abcdef")

    transport = httpx.MockTransport(handler)
    with TemporaryDirectory() as temp:
        async with httpx.AsyncClient(transport=transport) as http:
            with pytest.raises(RuntimeError, match="max_bytes"):
                await download_remote_file(
                    "https://cdn.example.test/file.txt",
                    dest_dir=temp,
                    http_client=http,
                    max_bytes=3,
                )
