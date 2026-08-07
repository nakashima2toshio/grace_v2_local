import { beforeEach, describe, expect, it } from 'vitest';

import { clearAllJobs, forgetJob, recallJob, rememberJob } from './activeJobs';

describe('activeJobs', () => {
  beforeEach(() => {
    clearAllJobs();
  });

  it('覚えた job_id を引ける', () => {
    rememberJob('chunking', 'job1');
    expect(recallJob('chunking')).toBe('job1');
  });

  it('覚えていない種別は undefined', () => {
    expect(recallJob('register')).toBeUndefined();
  });

  it('**種別ごとに独立している**（チャンク化と登録を同時に走らせられる）', () => {
    rememberJob('chunking', 'job1');
    rememberJob('register', 'job2');
    rememberJob('delete', 'job3');

    expect(recallJob('chunking')).toBe('job1');
    expect(recallJob('register')).toBe('job2');
    expect(recallJob('delete')).toBe('job3');
  });

  it('同じ種別を再実行したら上書きされる', () => {
    rememberJob('chunking', 'job1');
    rememberJob('chunking', 'job2');
    expect(recallJob('chunking')).toBe('job2');
  });

  it('forget すると引けなくなる（GC 済みジョブの後始末）', () => {
    rememberJob('delete', 'job1');
    forgetJob('delete');
    expect(recallJob('delete')).toBeUndefined();
  });

  it('覚えていない種別を forget しても落ちない', () => {
    expect(() => forgetJob('register')).not.toThrow();
  });

  it('forget は他の種別に影響しない', () => {
    rememberJob('chunking', 'job1');
    rememberJob('delete', 'job2');
    forgetJob('delete');

    expect(recallJob('chunking')).toBe('job1');
    expect(recallJob('delete')).toBeUndefined();
  });

  it('**モジュールスコープなので呼び出しをまたいで残る**', () => {
    // これが目的そのもの。React の state だとアンマウントで消えてしまう
    rememberJob('chunking', 'survives');
    const readInAnotherScope = () => recallJob('chunking');
    expect(readInAnotherScope()).toBe('survives');
  });
});
