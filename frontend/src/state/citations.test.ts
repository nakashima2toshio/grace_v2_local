import { describe, expect, it } from 'vitest';

import {
  citationSourceMix,
  contradictionNotice,
  escalateReferenceNotice,
  parseCitation,
} from './citations';

// 「明日の東京の天気は？」の実測: 社内 RAG は採用閾値 0.64 未達で 0 件、
// 出典 9 件はすべて Web。この状態で「社内ナレッジに基づく」と表示されていた。
const WEB_ONLY_CITATIONS = [
  '[Web] 東京の天気（https://weathernews.jp/onebox/tenki/tokyo/）',
  '[Web] 東京（東京）の天気 - Yahoo!天気・災害（https://weather.yahoo.co.jp/weather/jp/13/4410.html）',
  '[Web] 東京都の天気 - 日本気象協会 tenki.jp（https://tenki.jp/forecast/3/16/）',
];

describe('parseCitation', () => {
  it('Web 出典から URL を取り出す', () => {
    const parsed = parseCitation('[Web] 東京の天気（https://weathernews.jp/onebox/tenki/tokyo/）');

    expect(parsed).toEqual({
      kind: 'web',
      label: '東京の天気',
      url: 'https://weathernews.jp/onebox/tenki/tokyo/',
    });
  });

  it('クエリ文字列つきの URL も落とさない', () => {
    const parsed = parseCitation(
      '[Web] 天気予報（https://www.data.jma.go.jp/multi/yoho/yoho_detail.html?code=130010&lang=jp）',
    );

    expect(parsed.url).toBe(
      'https://www.data.jma.go.jp/multi/yoho/yoho_detail.html?code=130010&lang=jp',
    );
  });

  it('半角括弧でも取り出せる', () => {
    expect(parseCitation('[Web] タイトル(https://example.com)').url).toBe('https://example.com');
  });

  it('**URL が無い Web 出典は url=null**（ラベルだけ出す）', () => {
    expect(parseCitation('[Web] 東京の天気')).toEqual({
      kind: 'web',
      label: '東京の天気',
      url: null,
    });
  });

  it('社内出典はリンクにしない', () => {
    expect(parseCitation('[社内] qa_pairs_combined_chunks.csv')).toEqual({
      kind: 'internal',
      label: 'qa_pairs_combined_chunks.csv',
      url: null,
    });
  });

  it('接頭辞が無いものは社内扱い', () => {
    expect(parseCitation('faq.csv').kind).toBe('internal');
  });

  it('**タイトル中の URL はリンクにしない**（末尾の括弧だけを見る）', () => {
    const parsed = parseCitation('[Web] https://example.com について');

    expect(parsed.url).toBeNull();
    expect(parsed.label).toBe('https://example.com について');
  });

  it('ラベルが空なら URL 自体を表示に使う', () => {
    expect(parseCitation('[Web] （https://example.com）')).toEqual({
      kind: 'web',
      label: 'https://example.com',
      url: 'https://example.com',
    });
  });

  it('実測で取得された 9 件をすべてリンク化できる', () => {
    // 「明日の東京の天気は？」で SerpAPI が返した実際の出典
    const citations = [
      '[Web] 東京の天気（https://weathernews.jp/onebox/tenki/tokyo/）',
      '[Web] 東京（東京）の天気 - Yahoo!天気・災害（https://weather.yahoo.co.jp/weather/jp/13/4410.html）',
      '[Web] 東京都の天気 - 日本気象協会 tenki.jp（https://tenki.jp/forecast/3/16/）',
      '[Web] 東京の今日・明日の天気（https://www.toshin.com/weather/detail?id=56682）',
      '[Web] 天気予報 : 東京都東京地方（https://www.data.jma.go.jp/multi/yoho/yoho_detail.html?code=130010&lang=jp）',
      '[Web] 東京の天気予報(1時間・今日明日・週間)（https://weathernews.jp/onebox/tenki/tokyo/13100/）',
      '[Web] 新宿区の今日明日の天気（https://tenki.jp/forecast/3/16/4410/13104/）',
      '[Web] 東京都 明日の天気予報【NHK】（https://news.web.nhk/kishou-saigai/weather/pref/tokyo/tomorrow/）',
      '[Web] 東京の明日の天気予報（https://www.tokyo-np.co.jp/weather/area_tomorrow/0313）',
    ];

    const parsed = citations.map(parseCitation);

    expect(parsed).toHaveLength(9);
    expect(parsed.every((c) => c.kind === 'web')).toBe(true);
    expect(parsed.every((c) => c.url?.startsWith('https://'))).toBe(true);
    // ラベルに URL が残っていないこと（二重表示の防止）
    expect(parsed.every((c) => !c.label.includes('http'))).toBe(true);
  });
});

describe('citationSourceMix', () => {
  it('Web だけなら web', () => {
    expect(citationSourceMix(WEB_ONLY_CITATIONS)).toBe('web');
  });

  it('社内だけなら internal', () => {
    expect(citationSourceMix(['[社内] faq.csv', '[社内] policy.md'])).toBe('internal');
  });

  it('混在なら mixed', () => {
    expect(citationSourceMix(['[社内] faq.csv', ...WEB_ONLY_CITATIONS])).toBe('mixed');
  });

  it('出典ゼロなら none', () => {
    expect(citationSourceMix([])).toBe('none');
  });
});

describe('escalateReferenceNotice', () => {
  it('**出典が Web だけなら「社内ナレッジ」と名乗らない**（実測の誤り）', () => {
    const notice = escalateReferenceNotice(WEB_ONLY_CITATIONS);

    expect(notice).toContain('Web 検索結果に基づく');
    expect(notice).not.toContain('社内ナレッジに基づく');
  });

  it('社内出典があるときは従来どおり社内ナレッジと名乗る', () => {
    expect(escalateReferenceNotice(['[社内] faq.csv'])).toContain('社内ナレッジに基づく');
  });

  it('混在時は両方を挙げる', () => {
    const notice = escalateReferenceNotice(['[社内] faq.csv', ...WEB_ONLY_CITATIONS]);

    expect(notice).toContain('社内ナレッジと Web 検索結果に基づく');
  });

  it('**出典ゼロ（強制エスカレ）ではどちらも名乗らない**', () => {
    const notice = escalateReferenceNotice([]);

    expect(notice).not.toContain('社内ナレッジに基づく');
    expect(notice).not.toContain('Web 検索結果に基づく');
    expect(notice).toContain('出典は取得できていません');
  });

  it('どの内訳でも引き継ぎの文が残る', () => {
    for (const citations of [[], ['[社内] faq.csv'], WEB_ONLY_CITATIONS]) {
      expect(escalateReferenceNotice(citations)).toContain('有人対応へ引き継ぎます');
    }
  });
});

describe('contradictionNotice', () => {
  it('**社内出典が無いのに「社内ナレッジと Web」と書かない**', () => {
    expect(contradictionNotice(WEB_ONLY_CITATIONS)).not.toContain('社内ナレッジと Web');
  });

  it('社内と Web が揃っているときだけそう名乗る', () => {
    expect(contradictionNotice(['[社内] faq.csv', ...WEB_ONLY_CITATIONS])).toContain(
      '社内ナレッジと Web 情報で食い違い',
    );
  });
});
