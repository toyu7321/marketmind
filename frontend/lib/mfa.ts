import QRCode from 'qrcode';

export const MARKETMIND_TOTP_FACTOR_NAME = 'MarketMind authenticator';

export type MfaFactorSummary = {
  id: string;
  factor_type: string;
  status: string;
  friendly_name?: string;
};

export type TotpSetupPlan =
  | {kind: 'challenge'; factorId: string}
  | {kind: 'restart'; staleFactorIds: string[]}
  | {kind: 'enroll'};

/**
 * Decide whether this session should step up with an existing TOTP factor or
 * begin enrollment. Supabase Auth factors live outside MarketMind's database,
 * so a verified factor can outlive an application database migration and any
 * newly-created ActiveSession row.
 */
export function planTotpSetup(factors: readonly MfaFactorSummary[]): TotpSetupPlan {
  const verified = factors.find(factor => factor.factor_type === 'totp' && factor.status === 'verified');
  if (verified) return {kind: 'challenge', factorId: verified.id};

  const staleFactorIds = factors
    .filter(factor => factor.factor_type === 'totp'
      && factor.status === 'unverified'
      && factor.friendly_name === MARKETMIND_TOTP_FACTOR_NAME)
    .map(factor => factor.id);
  return staleFactorIds.length ? {kind: 'restart', staleFactorIds} : {kind: 'enroll'};
}

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
