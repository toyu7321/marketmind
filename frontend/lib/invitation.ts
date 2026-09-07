export type InvitationCallbackMarker = {
  type: string;
  errorCode: string;
  resume: boolean;
};

export type PasswordCheck = {label: string; passed: boolean};
export type InvitationEntry = 'form'|'dashboard'|'expired'|'invalid';

export function readInvitationCallbackMarker(href: string): InvitationCallbackMarker {
  const url = new URL(href);
  const fragment = new URLSearchParams(url.hash.startsWith('#') ? url.hash.slice(1) : url.hash);
  return {
    // Deliberately read only non-secret routing/error fields. Supabase's client
    // consumes and clears all session material itself.
    type: fragment.get('type') || url.searchParams.get('type') || '',
    errorCode: fragment.get('error_code') || url.searchParams.get('error_code') || url.searchParams.get('reason') || '',
    resume: url.searchParams.get('resume') === '1',
  };
}

export function passwordChecks(password: string): PasswordCheck[] {
  return [
    {label: 'At least 12 characters', passed: password.length >= 12},
    {label: 'Upper and lowercase letters', passed: /[a-z]/.test(password) && /[A-Z]/.test(password)},
    {label: 'At least one number', passed: /\d/.test(password)},
    {label: 'At least one symbol', passed: /[^A-Za-z0-9]/.test(password)},
  ];
}

export function validateInvitePassword(password: string, confirmation: string): string {
  if (!passwordChecks(password).every(check => check.passed)) return 'Choose a password that meets every requirement.';
  if (password !== confirmation) return 'The password confirmation does not match.';
  return '';
}

export function decideInvitationEntry(
  marker: InvitationCallbackMarker,
  authenticated: boolean,
  onboardingStatus?: 'pending'|'accepted',
): InvitationEntry {
  if (authenticated && onboardingStatus === 'accepted') return 'dashboard';
  if (!authenticated) return marker.errorCode ? 'expired' : 'invalid';
  if (marker.errorCode) return 'expired';
  if (onboardingStatus === 'pending' && (marker.type === 'invite' || marker.resume)) return 'form';
  return 'invalid';
}

export function invitationErrorMessage(marker: InvitationCallbackMarker): string {
  if (marker.errorCode.includes('expired') || marker.errorCode.startsWith('otp_')) {
    return 'This invitation has expired or was already used. Ask your administrator to send a new invitation.';
  }
  return 'This invitation is invalid or cannot be verified. Ask your administrator to send a new invitation.';
}
