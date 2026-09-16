export default [
  {
    languageOptions: {
      ecmaVersion: 2022,
      sourceType: "script",
      globals: {
        window: "readonly",
        document: "readonly",
        location: "readonly",
        sessionStorage: "readonly",
        WebSocket: "readonly",
        EventSource: "readonly",
        fetch: "readonly",
        URL: "readonly",
        URLSearchParams: "readonly",
        setInterval: "readonly",
        clearInterval: "readonly",
        setTimeout: "readonly",
        clearTimeout: "readonly",
        console: "readonly",
        Map: "readonly",
        Set: "readonly",
        JSON: "readonly",
        AudioContext: "readonly",
        webkitAudioContext: "readonly",
        Math: "readonly",
        Float32Array: "readonly",
        Int16Array: "readonly",
        DataView: "readonly",
        Blob: "readonly",
        requestAnimationFrame: "readonly",
        navigator: "readonly",
        localStorage: "readonly",
        performance: "readonly",
        alert: "readonly",
        confirm: "readonly",
        prompt: "readonly"
      }
    },
    rules: {
      "no-undef": "error"
    }
  }
];
