import QRCode from 'qrcode';

export function extractTotpSetupKey(uri: string) {
  try {
    const parsed = new URL(uri);
    const secret = parsed.protocol === 'otpauth:' && parsed.hostname === 'totp' ? parsed.searchParams.get('secret') : '';
    return secret && /^[A-Z2-7]+$/i.test(secret) ? secret.toUpperCase() : '';
  } catch { return ''; }
}

export function maskedTotpSetupKey() {
  return '•••• •••• •••• •••• ••••';
}

export function createTotpQrCode(uri: string) {
  return QRCode.toDataURL(uri, {errorCorrectionLevel:'M', margin:1, width:256, color:{dark:'#08161b', light:'#f4fbf7'}});
}
