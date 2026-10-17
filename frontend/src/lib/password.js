// A random password from the browser's CSPRNG: letters, digits and a few
// symbols every form in the panel accepts (plain ASCII, nothing a shell or a
// URL treats specially). Rejection sampling, so every character is equally
// likely.
const CHARS = 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789!@#%^*_+-';

export function generatePassword(length = 20) {
  const out = [];
  const limit = 256 - (256 % CHARS.length);
  const buf = new Uint8Array(length * 2);
  while (out.length < length) {
    crypto.getRandomValues(buf);
    for (const b of buf) {
      if (b < limit) out.push(CHARS[b % CHARS.length]);
      if (out.length === length) break;
    }
  }
  return out.join('');
}
