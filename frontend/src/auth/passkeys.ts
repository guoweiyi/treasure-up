import { api, write } from '../api';

export interface PasskeyCapabilities {
  enabled: boolean;
  available: boolean;
  rp_id: string;
  rp_name: string;
  origin: string;
  reason: string | null;
  password_login: boolean;
}

export interface SavedPasskey {
  id: string;
  name: string;
  created_at: string;
  last_used_at: string | null;
  transports: string[];
  device_type: string;
  backed_up: boolean;
}

export function supportsPasskeys() {
  return (
    window.isSecureContext && typeof PublicKeyCredential !== 'undefined' && !!navigator.credentials
  );
}

function decode(value: string): ArrayBuffer {
  const text = atob(value.replace(/-/g, '+').replace(/_/g, '/'));
  return Uint8Array.from(text, (character) => character.charCodeAt(0)).buffer;
}

function encode(value: ArrayBuffer | null): string | null {
  if (value === null) return null;
  const bytes = new Uint8Array(value);
  let text = '';
  for (const byte of bytes) text += String.fromCharCode(byte);
  return btoa(text).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
}

function credentialJSON(credential: PublicKeyCredential) {
  const response = credential.response;
  const common = {
    id: credential.id,
    rawId: encode(credential.rawId),
    type: credential.type,
    authenticatorAttachment: credential.authenticatorAttachment,
    clientExtensionResults: credential.getClientExtensionResults(),
  };
  if ('attestationObject' in response) {
    const attestation = response as AuthenticatorAttestationResponse;
    return {
      ...common,
      response: {
        clientDataJSON: encode(attestation.clientDataJSON),
        attestationObject: encode(attestation.attestationObject),
        transports: attestation.getTransports?.() || [],
      },
    };
  }
  const assertion = response as AuthenticatorAssertionResponse;
  return {
    ...common,
    response: {
      clientDataJSON: encode(assertion.clientDataJSON),
      authenticatorData: encode(assertion.authenticatorData),
      signature: encode(assertion.signature),
      userHandle: encode(assertion.userHandle),
    },
  };
}

export const getPasskeyCapabilities = () =>
  api<PasskeyCapabilities>(
    `/auth/passkeys/capabilities?origin=${encodeURIComponent(window.location.origin)}`,
  );

export async function loginWithPasskey() {
  const options = await write('/auth/passkeys/login/options', {});
  const publicKey = { ...options.public_key, challenge: decode(options.public_key.challenge) };
  if (publicKey.allowCredentials)
    publicKey.allowCredentials = publicKey.allowCredentials.map((item: any) => ({
      ...item,
      id: decode(item.id),
    }));
  const credential = (await navigator.credentials.get({ publicKey })) as PublicKeyCredential | null;
  if (!credential) throw new Error('未完成通行密钥验证');
  return write('/auth/passkeys/login/verify', {
    challenge_id: options.challenge_id,
    credential: credentialJSON(credential),
  });
}

export async function registerPasskey(password: string, name: string) {
  const options = await write('/auth/passkeys/register/options', { password, name });
  const publicKey = {
    ...options.public_key,
    challenge: decode(options.public_key.challenge),
    user: { ...options.public_key.user, id: decode(options.public_key.user.id) },
    excludeCredentials: (options.public_key.excludeCredentials || []).map((item: any) => ({
      ...item,
      id: decode(item.id),
    })),
  };
  const credential = (await navigator.credentials.create({
    publicKey,
  })) as PublicKeyCredential | null;
  if (!credential) throw new Error('未创建通行密钥');
  return write('/auth/passkeys/register/verify', {
    challenge_id: options.challenge_id,
    credential: credentialJSON(credential),
    name,
  });
}

export function passkeyError(error: unknown): string {
  if (error instanceof DOMException) {
    if (error.name === 'NotAllowedError' || error.name === 'AbortError')
      return '验证已取消或超时，可重试或使用密码登录';
    if (error.name === 'InvalidStateError') return '此设备已有该账号的通行密钥';
    if (error.name === 'SecurityError')
      return '当前站点地址不支持通行密钥，请使用配置的 HTTPS 域名或 localhost';
    if (error.name === 'NotSupportedError') return '此浏览器暂不支持，请使用密码登录';
  }
  return error instanceof Error ? error.message : '通行密钥操作失败';
}
