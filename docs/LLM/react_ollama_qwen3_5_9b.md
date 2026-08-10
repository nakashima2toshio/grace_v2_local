
# React `useReducer` カウンターの解説

## 1. 初期状態の定義

```jsx
const initialState = { count: 0 };
```

### 1行ごとの解説

```jsx
const initialState = { count: 0 };
```

- `const`  
  再代入できない定数を宣言します。

- `initialState`  
  `useReducer` が最初に使用する状態を格納する変数名です。

- `{ count: 0 }`  
  状態を表すオブジェクトです。  
  `count` の初期値を `0` に設定しています。

---

## 2. reducer関数の定義

```jsx
const reducer = (state, action) => {
  switch (action.type) {
    case 'increment':
      return { count: state.count + 1 };
    case 'decrement':
      return { count: state.count - 1 };
    default:
      throw new Error();
  }
};
```

### 1行ごとの解説

```jsx
const reducer = (state, action) => {
```

- `reducer` という名前のアロー関数を定義しています。
- `state` は現在の状態です。
- `action` は、状態をどのように変更するかを表すオブジェクトです。
- この関数は、新しい状態を返します。

```jsx
  switch (action.type) {
```

- `action.type` の値によって、実行する処理を切り替えます。
- 例えば、`action.type` には `'increment'` や `'decrement'` が入ります。

```jsx
    case 'increment':
```

- `action.type` が `'increment'` の場合に、次の処理を実行します。

```jsx
      return { count: state.count + 1 };
```

- 現在の `count` に `1` を加えた、新しい状態オブジェクトを返します。
- Reactの状態を直接変更せず、新しいオブジェクトを作成しています。

```jsx
    case 'decrement':
```

- `action.type` が `'decrement'` の場合に、次の処理を実行します。

```jsx
      return { count: state.count - 1 };
```

- 現在の `count` から `1` を引いた、新しい状態オブジェクトを返します。

```jsx
    default:
```

- どの `case` にも一致しなかった場合の処理です。

```jsx
      throw new Error();
```

- 想定していない `action.type` が渡されたことを示すエラーを発生させます。

```jsx
  }
```

- `switch` 文を終了します。

```jsx
};
```

- `reducer` 関数の定義を終了します。

---

## 3. Counterコンポーネントの定義

```jsx
const Counter = () => {
  const [state, dispatch] = useReducer(reducer, initialState);

  return (
    <>
      Count: {state.count}
      <button onClick={() => dispatch({ type: 'decrement' })}>
        -
      </button>
      <button onClick={() => dispatch({ type: 'increment' })}>
        +
      </button>
    </>
  );
};
```

### 1行ごとの解説

```jsx
const Counter = () => {
```

- `Counter` というReactの関数コンポーネントを定義しています。
- `() => {}` は引数を受け取らないアロー関数です。
- 元コードの `const Counter => () {` は構文エラーです。

```jsx
  const [state, dispatch] = useReducer(reducer, initialState);
```

- Reactの `useReducer` フックを呼び出します。
- 第1引数の `reducer` は、状態を更新する方法を定義した関数です。
- 第2引数の `initialState` は、状態の初期値です。
- `state` には現在の状態が格納されます。
- `dispatch` は、`reducer` にアクションを送るための関数です。

```jsx
  return (
```

- コンポーネントが画面に表示するJSXを返します。

```jsx
    <>
```

- React Fragmentの開始タグです。
- 不要なHTML要素を追加せず、複数の要素をまとめられます。

```jsx
      Count: {state.count}
```

- `Count:` という文字と、現在の `count` の値を表示します。
- `{}` の中ではJavaScriptの式を実行できます。

```jsx
      <button onClick={() => dispatch({ type: 'decrement' })}>
```

- 値を減らすためのボタンです。
- ボタンがクリックされると、`onClick` に指定した関数が実行されます。
- `dispatch` に `{ type: 'decrement' }` を渡します。
- このアクションを受け取った `reducer` は、`count` を `1` 減らします。

```jsx
        -
```

- ボタンに `-` という文字を表示します。

```jsx
      </button>
```

- 減算ボタンを終了します。

```jsx
      <button onClick={() => dispatch({ type: 'increment' })}>
```

- 値を増やすためのボタンです。
- クリックすると、`dispatch` に `{ type: 'increment' }` を渡します。
- このアクションを受け取った `reducer` は、`count` を `1` 増やします。

```jsx
        +
```

- ボタンに `+` という文字を表示します。

```jsx
      </button>
```

- 加算ボタンを終了します。

```jsx
    </>
```

- React Fragmentを終了します。

```jsx
  );
```

- JSXを返す `return` 文を終了します。

```jsx
};
```

- `Counter` コンポーネントの定義を終了します。

---

## 4. 実行可能なコード全体

```jsx
import { useReducer } from 'react';

const initialState = { count: 0 };

const reducer = (state, action) => {
  switch (action.type) {
    case 'increment':
      return { count: state.count + 1 };

    case 'decrement':
      return { count: state.count - 1 };

    default:
      throw new Error(`未対応のactionです: ${action.type}`);
  }
};

const Counter = () => {
  const [state, dispatch] = useReducer(reducer, initialState);

  return (
    <>
      Count: {state.count}

      <button onClick={() => dispatch({ type: 'decrement' })}>
        -
      </button>

      <button onClick={() => dispatch({ type: 'increment' })}>
        +
      </button>
    </>
  );
};

export default Counter;
```

## 5. 処理の流れ

1. `initialState` により、`count` が `0` で初期化されます。
2. `Counter` コンポーネントが `state.count` を表示します。
3. `-` ボタンをクリックすると、`decrement` アクションが送られます。
4. `reducer` が `count` を `1` 減らします。
5. `+` ボタンをクリックすると、`increment` アクションが送られます。
6. `reducer` が `count` を `1` 増やします。
7. 状態が更新されるたびに、コンポーネントが再レンダリングされます。
