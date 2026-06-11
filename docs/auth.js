// Firebase Auth 共通モジュール
// 未ログインならログインフォームをオーバーレイ表示し、サインイン完了で解決する
import {
    getAuth,
    onAuthStateChanged,
    signInWithEmailAndPassword
} from 'https://www.gstatic.com/firebasejs/10.7.1/firebase-auth.js';

export function ensureSignedIn(app) {
    const auth = getAuth(app);
    return new Promise((resolve) => {
        onAuthStateChanged(auth, (user) => {
            if (user) {
                removeOverlay();
                resolve(user);
            } else {
                showOverlay(auth);
            }
        });
    });
}

export async function getIdToken(app) {
    const user = getAuth(app).currentUser;
    return user ? await user.getIdToken() : null;
}

function showOverlay(auth) {
    if (document.getElementById('auth-overlay')) return;

    const overlay = document.createElement('div');
    overlay.id = 'auth-overlay';
    overlay.style.cssText =
        'position:fixed;inset:0;background:rgba(0,0,0,0.85);z-index:9999;' +
        'display:flex;align-items:center;justify-content:center;';
    overlay.innerHTML = `
        <form id="auth-form" style="background:#fff;padding:24px;border-radius:12px;width:280px;
                display:flex;flex-direction:column;gap:12px;font-family:sans-serif;">
            <div style="font-weight:bold;font-size:16px;">ログイン</div>
            <input id="auth-email" type="email" placeholder="メールアドレス" required
                autocomplete="username"
                style="padding:10px;border:1px solid #ccc;border-radius:8px;font-size:14px;">
            <input id="auth-password" type="password" placeholder="パスワード" required
                autocomplete="current-password"
                style="padding:10px;border:1px solid #ccc;border-radius:8px;font-size:14px;">
            <div id="auth-error" style="color:#c00;font-size:12px;display:none;"></div>
            <button type="submit"
                style="padding:10px;border:none;border-radius:8px;background:#007aff;
                       color:#fff;font-size:14px;cursor:pointer;">ログイン</button>
        </form>`;
    document.body.appendChild(overlay);

    overlay.querySelector('#auth-form').addEventListener('submit', async (e) => {
        e.preventDefault();
        const errorEl = overlay.querySelector('#auth-error');
        errorEl.style.display = 'none';
        try {
            await signInWithEmailAndPassword(
                auth,
                overlay.querySelector('#auth-email').value,
                overlay.querySelector('#auth-password').value
            );
            // 成功すると onAuthStateChanged 経由でオーバーレイが消える
        } catch (err) {
            errorEl.textContent = 'ログインに失敗しました (' + (err.code || err.message) + ')';
            errorEl.style.display = 'block';
        }
    });
}

function removeOverlay() {
    const overlay = document.getElementById('auth-overlay');
    if (overlay) overlay.remove();
}
