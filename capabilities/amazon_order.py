"""
Amazon注文Capability

音声でAmazonの商品を検索・注文する能力:
- amazon_search: 商品を検索して候補を表示
- amazon_order: 確認後に注文を確定
"""

import os
import json
import asyncio
import stat
from typing import Any, Dict, Optional
from pathlib import Path
from urllib.parse import quote

from .base import Capability, CapabilityCategory, CapabilityResult
from config import Config

# Playwright（遅延インポート）
_browser = None
_context = None
_page = None
_playwright = None  # Playwrightインスタンスを保持

# 検索結果のキャッシュ
_search_results: list = []

# Cookieファイルのパス
COOKIE_FILE = os.path.join(Config.BASE_DIR, "amazon_cookies.json")


def _run_async(coro):
    """非同期コルーチンを同期的に実行（既存のイベントループを考慮）"""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop and loop.is_running():
        # 既にイベントループが動作中の場合、新しいスレッドで実行
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor() as executor:
            future = executor.submit(asyncio.run, coro)
            return future.result()
    else:
        # イベントループがない場合、新しく作成
        return asyncio.run(coro)


async def _get_browser():
    """Playwrightブラウザを取得（シングルトン）"""
    global _browser, _context, _page, _playwright

    if _browser is None:
        from playwright.async_api import async_playwright

        _playwright = await async_playwright().start()
        _browser = await _playwright.chromium.launch(
            headless=True,
            args=['--no-sandbox', '--disable-dev-shm-usage', '--disable-gpu']
        )

        # Cookieがあれば復元
        if os.path.exists(COOKIE_FILE):
            try:
                with open(COOKIE_FILE, 'r') as f:
                    cookies = json.load(f)
                _context = await _browser.new_context()
                await _context.add_cookies(cookies)
            except (json.JSONDecodeError, IOError):
                _context = await _browser.new_context()
        else:
            _context = await _browser.new_context()

        _page = await _context.new_page()

        # ユーザーエージェント設定
        await _page.set_extra_http_headers({
            'User-Agent': 'Mozilla/5.0 (X11; Linux aarch64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
        })

    return _page


async def _save_cookies():
    """Cookieを保存（パーミッション600で保護）"""
    global _context
    if _context:
        cookies = await _context.cookies()
        os.makedirs(os.path.dirname(COOKIE_FILE), exist_ok=True)
        with open(COOKIE_FILE, 'w') as f:
            json.dump(cookies, f)
        # ユーザーのみ読み書き可能に設定
        os.chmod(COOKIE_FILE, stat.S_IRUSR | stat.S_IWUSR)


async def _check_login_status(page) -> bool:
    """現在のページでログイン状態を確認"""
    try:
        account_element = await page.query_selector('#nav-link-accountList-nav-line-1')
        if account_element:
            text = await account_element.inner_text()
            return 'ログイン' not in text and 'サインイン' not in text
        return False
    except:
        return False


async def _search_products(query: str) -> list:
    """商品を検索"""
    global _search_results

    page = await _get_browser()

    # 検索ページに直接移動（日本語をURLエンコード）
    encoded_query = quote(query)
    search_url = f'https://www.amazon.co.jp/s?k={encoded_query}'
    await page.goto(search_url, timeout=60000)

    # ログイン状態確認
    is_logged_in = await _check_login_status(page)
    if not is_logged_in:
        return []

    # 検索結果を取得（上位5件）
    results = []
    items = await page.query_selector_all('[data-component-type="s-search-result"]')

    for i, item in enumerate(items[:5]):
        try:
            # 商品名
            title_elem = await item.query_selector('h2 span')
            title = await title_elem.inner_text() if title_elem else "不明"

            # 価格
            price_elem = await item.query_selector('.a-price-whole')
            price = await price_elem.inner_text() if price_elem else "価格不明"
            price = price.replace(',', '').replace('\n', '')

            # ASIN（商品ID）
            asin = await item.get_attribute('data-asin')

            # 商品URL
            link_elem = await item.query_selector('h2 a')
            href = await link_elem.get_attribute('href') if link_elem else ""
            url = f'https://www.amazon.co.jp{href}' if href.startswith('/') else href

            if asin and title != "不明":
                results.append({
                    'index': i + 1,
                    'title': title[:50] + ('...' if len(title) > 50 else ''),
                    'price': f'{price}円',
                    'asin': asin,
                    'url': url
                })
        except:
            continue

    _search_results = results
    await _save_cookies()
    return results


async def _add_to_cart_and_checkout(asin: str) -> Dict[str, Any]:
    """カートに追加して注文"""
    page = await _get_browser()

    # 商品ページへ
    product_url = f'https://www.amazon.co.jp/dp/{asin}'
    await page.goto(product_url, timeout=30000)

    # カートに追加
    add_to_cart = await page.query_selector('#add-to-cart-button')
    if not add_to_cart:
        # 別のボタン形式を試す
        add_to_cart = await page.query_selector('[name="submit.add-to-cart"]')

    if not add_to_cart:
        return {'success': False, 'message': 'カートに追加できませんでした'}

    await add_to_cart.click()
    await page.wait_for_timeout(2000)

    # レジに進む
    await page.goto('https://www.amazon.co.jp/gp/cart/view.html', timeout=30000)

    proceed_button = await page.query_selector('[name="proceedToRetailCheckout"]')
    if not proceed_button:
        proceed_button = await page.query_selector('#sc-buy-box-ptc-button')

    if not proceed_button:
        return {'success': False, 'message': 'レジに進めませんでした'}

    await proceed_button.click()
    await page.wait_for_timeout(3000)

    # 注文確定ボタン
    place_order = await page.query_selector('[name="placeYourOrder1"]')
    if not place_order:
        place_order = await page.query_selector('#submitOrderButtonId')
    if not place_order:
        place_order = await page.query_selector('.place-order-button')

    if not place_order:
        # 追加の確認が必要な場合があるので、現在のURLを返す
        current_url = page.url
        return {
            'success': False,
            'message': '注文確定画面に進めませんでした。ログインの確認が必要かもしれません。',
            'url': current_url
        }

    # 注文確定
    await place_order.click()
    await page.wait_for_timeout(3000)

    await _save_cookies()

    # 注文完了確認
    thank_you = await page.query_selector('.a-box-inner h1')
    if thank_you:
        text = await thank_you.inner_text()
        if '注文' in text or 'ありがとう' in text:
            return {'success': True, 'message': '注文が完了しました'}

    return {'success': True, 'message': '注文処理を行いました'}


class AmazonSearch(Capability):
    """Amazonで商品を検索"""

    @property
    def name(self) -> str:
        return "amazon_search"

    @property
    def category(self) -> CapabilityCategory:
        return CapabilityCategory.SYSTEM

    @property
    def description(self) -> str:
        return """Amazonで商品を検索する。以下の場面で使う：

■ 商品を探すとき：
- 「Amazonでポテトチップス探して」
- 「〇〇をAmazonで検索」
- 「Amazonで〇〇いくら？」

queryで検索キーワードを渡す。
検索結果は番号付きで返されるので、ユーザーに「どれにしますか？」と確認する。"""

    def _get_parameters(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "検索キーワード（例: 'ポテトチップス', 'モバイルバッテリー'）"
                }
            },
            "required": ["query"]
        }

    def execute(self, query: str) -> CapabilityResult:
        """商品を検索"""
        try:
            # 非同期処理を同期的に実行
            results = _run_async(_search_products(query))

            if not results:
                return CapabilityResult.fail(
                    "Amazonにログインされていないようです。"
                    "スマホでAmazonにログインしてから、もう一度お試しください。"
                )

            # 結果を整形
            message_lines = [f"「{query}」の検索結果です：\n"]
            for r in results:
                message_lines.append(f"{r['index']}. {r['title']} - {r['price']}")

            message_lines.append("\nどれを注文しますか？番号でお答えください。")

            return CapabilityResult.ok(
                "\n".join(message_lines),
                data={'results': results}
            )

        except ImportError:
            return CapabilityResult.fail("注文機能がセットアップされていません")
        except Exception as e:
            return CapabilityResult.fail("検索できませんでした")


class AmazonOrder(Capability):
    """Amazonで商品を注文"""

    @property
    def name(self) -> str:
        return "amazon_order"

    @property
    def category(self) -> CapabilityCategory:
        return CapabilityCategory.SYSTEM

    @property
    def requires_confirmation(self) -> bool:
        return True

    @property
    def description(self) -> str:
        return """Amazonで商品を注文確定する。amazon_searchの後に使う：

■ 注文するとき：
- ユーザーが「1番」「最初の」など番号で指定したとき
- 「それを注文して」と言われたとき

item_indexで選択された商品の番号を渡す（1始まり）。
注文前に必ず「〇〇を注文しますね？」と確認すること。"""

    def _get_parameters(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "item_index": {
                    "type": "integer",
                    "description": "注文する商品の番号（1から始まる）"
                }
            },
            "required": ["item_index"]
        }

    def execute(self, item_index: int) -> CapabilityResult:
        """商品を注文"""
        global _search_results

        try:
            # インデックスの確認
            if not _search_results:
                return CapabilityResult.fail(
                    "先に商品を検索してください。「Amazonで〇〇を検索」と言ってください。"
                )

            if item_index < 1 or item_index > len(_search_results):
                return CapabilityResult.fail(
                    f"1から{len(_search_results)}の番号で選んでください。"
                )

            item = _search_results[item_index - 1]

            # 非同期処理を同期的に実行
            result = _run_async(_add_to_cart_and_checkout(item['asin']))

            if result['success']:
                return CapabilityResult.ok(
                    f"{item['title']}を注文しました。{item['price']}です。"
                )
            else:
                return CapabilityResult.fail(result['message'])

        except ImportError:
            return CapabilityResult.fail("注文機能がセットアップされていません")
        except Exception as e:
            return CapabilityResult.fail("注文できませんでした")


async def setup_amazon_login():
    """
    Amazonにログインするためのセットアップ（初回のみ手動で実行）

    使い方:
    1. python -c "import asyncio; from capabilities.amazon_order import setup_amazon_login; asyncio.run(setup_amazon_login())"
    2. ブラウザが開くのでAmazonにログイン
    3. ログイン完了後、Enterを押す
    """
    from playwright.async_api import async_playwright

    playwright = await async_playwright().start()
    browser = await playwright.chromium.launch(headless=False)  # GUIで開く
    context = await browser.new_context()
    page = await context.new_page()

    await page.goto('https://www.amazon.co.jp/ap/signin')

    print("\n" + "="*50)
    print("ブラウザでAmazonにログインしてください。")
    print("ログイン完了後、このターミナルでEnterを押してください。")
    print("="*50 + "\n")

    input("ログイン完了後、Enterを押してください...")

    # Cookieを保存（パーミッション600で保護）
    cookies = await context.cookies()
    os.makedirs(os.path.dirname(COOKIE_FILE), exist_ok=True)
    with open(COOKIE_FILE, 'w') as f:
        json.dump(cookies, f)
    os.chmod(COOKIE_FILE, stat.S_IRUSR | stat.S_IWUSR)

    print(f"\nCookieを保存しました: {COOKIE_FILE}")
    print("これで音声からAmazon注文が可能になりました。")

    await browser.close()
    await playwright.stop()


# エクスポート
AMAZON_CAPABILITIES = [
    AmazonSearch(),
    AmazonOrder(),
]
