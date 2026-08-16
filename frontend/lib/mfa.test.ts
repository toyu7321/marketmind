import {describe, expect, it} from 'vitest';
import {extractTotpSetupKey, maskedTotpSetupKey} from './mfa';

describe('MFA setup material', () => {
  it('extracts a valid TOTP setup key without exposing the enrollment URI', () => {
    expect(extractTotpSetupKey('otpauth://totp/MarketMind:test?secret=JBSWY3DPEHPK3PXP&issuer=MarketMind')).toBe('JBSWY3DPEHPK3PXP');
  });

  it('does not treat non-TOTP values or malformed setup keys as usable keys', () => {
    expect(extractTotpSetupKey('https://example.test/?secret=JBSWY3DPEHPK3PXP')).toBe('');
    expect(extractTotpSetupKey('otpauth://totp/MarketMind:test?secret=not-a-totp-key')).toBe('');
  });

  it('uses a constant masked value until the user explicitly reveals the key', () => {
    expect(maskedTotpSetupKey()).toBe('•••• •••• •••• •••• ••••');
  });
});
