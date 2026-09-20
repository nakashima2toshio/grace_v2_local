import { describe, expect, it } from 'vitest';

import { looksUnreachable, metaErrorMessage } from './metaFetch';

describe('looksUnreachable', () => {
  it('TypeError（fetch 自体の失敗）は到達不能とみなす', () => {
    // プロキシを経由しない構成では fetch が TypeError: Failed to fetch で落ちる
    expect(looksUnreachable(new TypeError('Failed to fetch'))).toBe(true);
  });

  it('**500 は到達不能とみなす**（Vite プロキシは転送先へ繋げないと 500 を返す）', () => {
    expect(looksUnreachable(new Error('API エラー (500): Internal Server Error'))).toBe(true);
  });

  it('502 / 504 も到達不能とみなす', () => {
    expect(looksUnreachable(new Error('API エラー (502): Bad Gateway'))).toBe(true);
    expect(looksUnreachable(new Error('API エラー (504): Gateway Timeout'))).toBe(true);
  });

  it('4xx は到達不能とみなさない（サーバには届いている）', () => {
    expect(looksUnreachable(new Error('API エラー (404): Not Found'))).toBe(false);
    expect(looksUnreachable(new Error('API エラー (422): Unprocessable'))).toBe(false);
  });

  it('Error でない値でも落ちない', () => {
    expect(looksUnreachable('なにかの文字列')).toBe(false);
    expect(looksUnreachable(null)).toBe(false);
    expect(looksUnreachable(undefined)).toBe(false);
  });
});

describe('metaErrorMessage', () => {
  it('**到達不能なら復旧手順を含める**', () => {
    const message = metaErrorMessage(new TypeError('Failed to fetch'), '業界プロファイル');

    expect(message).toContain('業界プロファイル');
    expect(message).toContain('localhost:8000');
    expect(message).toContain('./run_dev.sh');
  });

  it('ルールセットでも同じ形で出る', () => {
    const message = metaErrorMessage(new Error('API エラー (500): x'), 'ルールセット');

    expect(message).toContain('ルールセット');
    expect(message).toContain('./run_dev.sh');
  });

  it('到達しているエラーでは起動手順を出さない（的外れな案内をしない）', () => {
    const message = metaErrorMessage(new Error('API エラー (404): Not Found'), 'ルールセット');

    expect(message).toContain('ルールセット');
    expect(message).not.toContain('./run_dev.sh');
  });

  it('**原因の詳細を必ず残す**（握りつぶさない）', () => {
    const message = metaErrorMessage(new Error('API エラー (500): boom'), '業界プロファイル');
    expect(message).toContain('boom');
  });

  it('Error でない値も文字列化して残す', () => {
    const message = metaErrorMessage({ weird: true }, '業界プロファイル');
    expect(message).toContain('業界プロファイル');
    expect(message.length).toBeGreaterThan(0);
  });
});
