import {describe, expect, it} from 'vitest';
import {decideInvitationEntry, passwordChecks, readInvitationCallbackMarker, validateInvitePassword} from './invitation';

describe('invited-user onboarding', () => {
  it('accepts a valid Supabase invite fragment without exposing session material', () => {
    const marker = readInvitationCallbackMarker('https://marketmind.example/auth/accept-invite#access_token=not-a-real-token&type=invite');
    expect(marker).toEqual({type: 'invite', errorCode: '', resume: false});
    expect(decideInvitationEntry(marker, true, 'pending')).toBe('form');
    expect(JSON.stringify(marker)).not.toContain('not-a-real-token');
  });

  it('supports the same fragment callback shape used by mobile Safari', () => {
    const marker = readInvitationCallbackMarker('https://marketmind.example/auth/accept-invite#expires_in=3600&refresh_token=redacted&type=invite');
    expect(decideInvitationEntry(marker, true, 'pending')).toBe('form');
  });

  it('requires a valid authenticated invitation rather than public signup', () => {
    const direct = readInvitationCallbackMarker('https://marketmind.example/auth/accept-invite');
    expect(decideInvitationEntry(direct, false)).toBe('invalid');
    expect(decideInvitationEntry({...direct, resume: true}, false, 'pending')).toBe('invalid');
  });

  it('shows a safe expired state for an invalid or reused provider link', () => {
    const expired = readInvitationCallbackMarker('https://marketmind.example/auth/accept-invite#error=access_denied&error_code=otp_expired');
    expect(decideInvitationEntry(expired, false)).toBe('expired');
  });

  it('redirects an already activated user instead of reopening password setup', () => {
    const invite = {type: 'invite', errorCode: '', resume: false};
    expect(decideInvitationEntry(invite, true, 'accepted')).toBe('dashboard');
  });

  it('validates password strength and confirmation before activation', () => {
    expect(passwordChecks('StrongPassword9!').every(check => check.passed)).toBe(true);
    expect(validateInvitePassword('StrongPassword9!', 'different')).toBe('The password confirmation does not match.');
    expect(validateInvitePassword('weak', 'weak')).toBe('Choose a password that meets every requirement.');
    expect(validateInvitePassword('StrongPassword9!', 'StrongPassword9!')).toBe('');
  });
});
