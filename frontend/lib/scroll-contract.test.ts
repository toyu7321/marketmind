import {readFileSync} from 'node:fs';
import {fileURLToPath} from 'node:url';
import {describe, expect, it} from 'vitest';

const styles = readFileSync(fileURLToPath(new URL('../app/refinements.css', import.meta.url)), 'utf8');
const serviceWorker = readFileSync(fileURLToPath(new URL('../public/sw.js', import.meta.url)), 'utf8');

describe('scrolling and cache safety contract', () => {
  it('keeps vertical page scrolling on the document while tables only overflow horizontally', () => {
    expect(styles).toContain('html,body{overscroll-behavior-y:auto}');
    expect(styles).toContain('.table-scroll{overflow-x:auto;overflow-y:clip');
    expect(styles).not.toContain('addEventListener(\'wheel\'');
  });

  it('keeps authenticated API responses out of the service-worker cache', () => {
    expect(serviceWorker).toContain("if (url.pathname.startsWith('/api/')) return;");
    expect(serviceWorker).toContain("const safeStatic = url.pathname.startsWith('/_next/static/')");
  });
});
