function addSupportEntry() {
  var btn = document.createElement('a');
  btn.href = '/web/chat.html';
  btn.textContent = '客服';
  btn.style.position = 'fixed';
  btn.style.right = '16px';
  btn.style.bottom = '16px';
  btn.style.background = '#1677ff';
  btn.style.color = '#fff';
  btn.style.padding = '12px 16px';
  btn.style.borderRadius = '24px';
  btn.style.textDecoration = 'none';
  btn.style.boxShadow = '0 4px 12px rgba(0,0,0,0.15)';
  btn.style.zIndex = '9999';
  document.body.appendChild(btn);
}
