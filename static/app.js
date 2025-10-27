const tg = window.Telegram.WebApp;
tg.ready();

let currentUser = null;
let decisions = {};  // {order_id: 'yes/no/skip'}
let saved = false;
const backendUrl = 'https://your-backend-url';  // Ngrok yoki GCP URL ni qo'ying, masalan 'https://00c7f07450a1.ngrok-free.app'

// Sahifalarni ko'rsatish funksiyasi (agar bitta HTML bo'lsa)
function showPage(pageId) {
    document.querySelectorAll('.page').forEach(page => page.classList.remove('active'));
    document.getElementById(pageId).classList.add('active');
}

// Agar alohida HTML fayllar bo'lsa, sahifalarni dinamik yuklash (lekin TMA uchun tavsiya etilmaydi, bitta HTML yaxshi)
function loadPage(url, containerId) {
    fetch(url)
        .then(response => response.text())
        .then(html => {
            document.getElementById(containerId).innerHTML = html;
            // Skriptlarni qayta ishlatish uchun eval qilish (xavfsiz emas, lekin misol uchun)
            const scripts = document.getElementById(containerId).querySelectorAll('script');
            scripts.forEach(script => eval(script.innerHTML));
        })
        .catch(err => console.error('Sahifa yuklash xatosi:', err));
}

// Login funksiyasi (login.html yoki bitta sahifada)
function login() {
    const username = document.getElementById('username').value;
    const password = document.getElementById('password').value;
    // Mock: Realda backendda password check qiling
    const authData = { ...tg.initDataUnsafe, username, password };
    fetch(`${backendUrl}/auth`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(authData)
    })
    .then(res => {
        if (!res.ok) throw new Error('Auth xatosi');
        return res.json();
    })
    .then(data => {
        currentUser = data.user;
        if (currentUser.role === 'admin') {
            showPage('admin-page');  // Yoki loadPage('admin.html', 'main-container');
            loadAdminData();
        } else {
            showPage('orders-page');  // Yoki loadPage('orders.html', 'main-container');
            loadCampaigns();
        }
        tg.MainButton.show();  // Saqlash tugmasini ko'rsatish
    })
    .catch(err => {
        alert('Login xatosi: ' + err.message);
        console.error(err);
    });
}

// Kampaniyalar va buyurtmalarni yuklash (orders.html yoki sahifada)
function loadCampaigns() {
    if (!currentUser.assigned_campaigns || currentUser.assigned_campaigns.length === 0) {
        alert('Sizga taqsimlangan kampaniyalar yo‘q.');
        return;
    }
    const select = document.getElementById('campaign-select');
    select.innerHTML = '';  // Tozalash
    currentUser.assigned_campaigns.forEach(camp => {
        const option = document.createElement('option');
        option.value = camp;
        option.textContent = `Kampaniya ID: ${camp}`;
        select.appendChild(option);
    });
    select.addEventListener('change', loadOrders);
    // Avtomatik birinchisini yuklash
    if (select.options.length > 0) loadOrders();
}

function loadOrders() {
    const campaignId = document.getElementById('campaign-select').value;
    if (!campaignId) return;
    fetch(`${backendUrl}/orders/${campaignId}`, {
        method: 'GET',
        headers: { 'Content-Type': 'application/json' }
    })
    .then(res => {
        if (!res.ok) throw new Error('Buyurtmalar yuklash xatosi');
        return res.json();
    })
    .then(orders => {
        const list = document.getElementById('orders-list');
        list.innerHTML = '';
        if (orders.length === 0) {
            list.innerHTML = '<p>Buyurtmalar topilmadi.</p>';
            return;
        }
        orders.forEach(order => {
            const card = document.createElement('div');
            card.className = 'order-card';
            card.innerHTML = `
                <h3>${order.product_name}</h3>
                <img src="${order.image_path || 'placeholder.jpg'}" alt="${order.product_name}" onerror="this.src='placeholder.jpg'">
                <p>SKU: ${order.sku} | Miqdori: ${order.quantity} | Buyurtma ID: ${order.order_id} | Shtrix-kod: ${order.barcode}</p>
                <button onclick="decide('${order.order_id}', 'yes')">✅ Tasdiqlash</button>
                <button onclick="decide('${order.order_id}', 'no')">❌ Bekor qilish</button>
                <button onclick="decide('${order.order_id}', 'skip')">➖ O‘tkazib yuborish</button>
                <button onclick="scanBarcode('${order.order_id}', '${order.barcode}')">📷 Shtrix-kodni skanlash</button>
            `;
            list.appendChild(card);
        });
    })
    .catch(err => {
        alert('Buyurtmalar yuklash xatosi: ' + err.message);
        console.error(err);
    });
}

function decide(orderId, decision) {
    decisions[orderId] = decision;
    console.log(`Qaror: ${orderId} -> ${decision}`);
    // UI da ko'rsatish (masalan, tugma rangini o'zgartirish)
}

function scanBarcode(orderId, expectedBarcode) {
    tg.showScanQrPopup({ text: 'Shtrix-kodni skanlang' }, (scannedData) => {
        if (scannedData) {
            const scannedBarcode = scannedData;  // Assume scannedData - shtrix-kod
            if (scannedBarcode === expectedBarcode) {
                alert('Shtrix-kod mos keldi! Avtomatik tasdiqlash.');
                decide(orderId, 'yes');
            } else {
                alert(`Shtrix-kod mos kelmaydi: ${scannedBarcode} (Kutilgan: ${expectedBarcode})`);
            }
        } else {
            alert('Skanlash bekor qilindi.');
        }
    });
}

function saveDecisions() {
    if (Object.keys(decisions).length === 0) {
        alert('Hech qanday qaror qabul qilinmagan.');
        return;
    }
    const decisionsList = Object.entries(decisions).map(([order_id, decision]) => ({ order_id, decision }));
    fetch(`${backendUrl}/save_decisions`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(decisionsList)
    })
    .then(res => {
        if (!res.ok) throw new Error('Saqlash xatosi');
        return res.json();
    })
    .then(() => {
        saved = true;
        alert('Qarorlar saqlandi va hisobotlar yuborildi!');
        decisions = {};  // Tozalash
        tg.close();  // App ni yopish
    })
    .catch(err => {
        alert('Saqlash xatosi: ' + err.message);
        console.error(err);
    });
}

// Admin funksiyalari (admin.html yoki sahifada)
function loadAdminData() {
    // Kampaniyalar yuklash
    fetch(`${backendUrl}/campaigns`, {
        method: 'GET',
        headers: { 'Content-Type': 'application/json' }
    })
    .then(res => res.json())
    .then(campaigns => {
        const select = document.getElementById('admin-campaign-select');
        select.innerHTML = '';
        campaigns.forEach(camp => {
            const option = document.createElement('option');
            option.value = camp.id;  // Assume 'id' bor, Yandex response bo'yicha
            option.textContent = `Kampaniya: ${camp.id} (${camp.name || ''})`;
            select.appendChild(option);
        });
    })
    .catch(err => console.error('Kampaniyalar yuklash xatosi:', err));

    // Statistika va ishchilar yuklash
    fetch(`${backendUrl}/stats`, {
        method: 'GET',
        headers: { 'Content-Type': 'application/json' }
    })
    .then(res => res.json())
    .then(stats => {
        const list = document.getElementById('stats-list');
        list.innerHTML = '';
        const userSelect = document.getElementById('admin-user-select');
        userSelect.innerHTML = '';
        stats.forEach(user => {
            const div = document.createElement('div');
            div.innerHTML = `
                <p><strong>${user.username}</strong>: Taqsimlangan kampaniyalar - ${user.assigned_campaigns.join(', ') || 'Yo‘q'} | Ishlangan buyurtmalar: ${user.processed_orders}</p>
            `;
            list.appendChild(div);

            // Ishchilarni select ga qo'shish
            const option = document.createElement('option');
            option.value = user.username;
            option.textContent = user.username;
            userSelect.appendChild(option);
        });
    })
    .catch(err => console.error('Statistika yuklash xatosi:', err));
}

function createUser() {
    const username = document.getElementById('new-username').value.trim();
    const password = document.getElementById('new-password').value.trim();
    if (!username || !password) {
        alert('Username va password ni kiriting!');
        return;
    }
    fetch(`${backendUrl}/create_user`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ username, password, role: 'worker' })
    })
    .then(res => {
        if (!res.ok) throw new Error('Yaratish xatosi');
        alert('Yangi ishchi yaratildi!');
        document.getElementById('new-username').value = '';
        document.getElementById('new-password').value = '';
        loadAdminData();  // Yangilash
    })
    .catch(err => alert('Xato: ' + err.message));
}

function assignCampaign() {
    const username = document.getElementById('admin-user-select').value;
    const campaignId = document.getElementById('admin-campaign-select').value;
    if (!username || !campaignId) {
        alert('Ishchi va kampaniyani tanlang!');
        return;
    }
    fetch(`${backendUrl}/assign_campaign?username=${encodeURIComponent(username)}&campaign_id=${campaignId}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' }
    })
    .then(res => {
        if (!res.ok) throw new Error('Taqsimlash xatosi');
        alert('Kampaniya taqsimlandi!');
        loadAdminData();  // Yangilash
    })
    .catch(err => alert('Xato: ' + err.message));
}

// Saqlash check (chiqishda ogohlantirish)
window.addEventListener('beforeunload', (e) => {
    if (!saved && Object.keys(decisions).length > 0) {
        e.preventDefault();
        e.returnValue = '';
        tg.showPopup({
            message: 'Saqlashni unutdingiz! Saqlash tugmasini bosing.',
            buttons: [{ type: 'ok', text: 'OK' }]
        });
    }
});

// Main Button sozlash (Telegram SDK)
tg.MainButton.setParams({
    text: 'Saqlash',
    color: '#007BFF',
    text_color: '#FFFFFF'
});
tg.MainButton.onClick(saveDecisions);

// Init: Agar login sahifasi bo'lsa, ko'rsatish
if (document.getElementById('login-page')) {
    showPage('login-page');
} else {
    // Agar alohida fayllar bo'lsa, login.html ni yuklash
    loadPage('login.html', 'body');  // Misol, lekin TMA da bitta URL
}

// Error handling va log
console.log('App.js yuklandi. Backend URL:', backendUrl);