(function() {
  "use strict";
  function getDefaultExportFromCjs(x) {
    return x && x.__esModule && Object.prototype.hasOwnProperty.call(x, "default") ? x["default"] : x;
  }
  var jsxRuntime = { exports: {} };
  var reactJsxRuntime_production_min = {};
  var react = { exports: {} };
  var react_production_min = {};
  /**
   * @license React
   * react.production.min.js
   *
   * Copyright (c) Facebook, Inc. and its affiliates.
   *
   * This source code is licensed under the MIT license found in the
   * LICENSE file in the root directory of this source tree.
   */
  var hasRequiredReact_production_min;
  function requireReact_production_min() {
    if (hasRequiredReact_production_min) return react_production_min;
    hasRequiredReact_production_min = 1;
    var l = Symbol.for("react.element"), n = Symbol.for("react.portal"), p = Symbol.for("react.fragment"), q = Symbol.for("react.strict_mode"), r = Symbol.for("react.profiler"), t = Symbol.for("react.provider"), u = Symbol.for("react.context"), v = Symbol.for("react.forward_ref"), w = Symbol.for("react.suspense"), x = Symbol.for("react.memo"), y = Symbol.for("react.lazy"), z = Symbol.iterator;
    function A(a) {
      if (null === a || "object" !== typeof a) return null;
      a = z && a[z] || a["@@iterator"];
      return "function" === typeof a ? a : null;
    }
    var B = { isMounted: function() {
      return false;
    }, enqueueForceUpdate: function() {
    }, enqueueReplaceState: function() {
    }, enqueueSetState: function() {
    } }, C = Object.assign, D = {};
    function E(a, b, e) {
      this.props = a;
      this.context = b;
      this.refs = D;
      this.updater = e || B;
    }
    E.prototype.isReactComponent = {};
    E.prototype.setState = function(a, b) {
      if ("object" !== typeof a && "function" !== typeof a && null != a) throw Error("setState(...): takes an object of state variables to update or a function which returns an object of state variables.");
      this.updater.enqueueSetState(this, a, b, "setState");
    };
    E.prototype.forceUpdate = function(a) {
      this.updater.enqueueForceUpdate(this, a, "forceUpdate");
    };
    function F() {
    }
    F.prototype = E.prototype;
    function G(a, b, e) {
      this.props = a;
      this.context = b;
      this.refs = D;
      this.updater = e || B;
    }
    var H = G.prototype = new F();
    H.constructor = G;
    C(H, E.prototype);
    H.isPureReactComponent = true;
    var I = Array.isArray, J = Object.prototype.hasOwnProperty, K = { current: null }, L = { key: true, ref: true, __self: true, __source: true };
    function M(a, b, e) {
      var d, c = {}, k = null, h = null;
      if (null != b) for (d in void 0 !== b.ref && (h = b.ref), void 0 !== b.key && (k = "" + b.key), b) J.call(b, d) && !L.hasOwnProperty(d) && (c[d] = b[d]);
      var g = arguments.length - 2;
      if (1 === g) c.children = e;
      else if (1 < g) {
        for (var f = Array(g), m = 0; m < g; m++) f[m] = arguments[m + 2];
        c.children = f;
      }
      if (a && a.defaultProps) for (d in g = a.defaultProps, g) void 0 === c[d] && (c[d] = g[d]);
      return { $$typeof: l, type: a, key: k, ref: h, props: c, _owner: K.current };
    }
    function N(a, b) {
      return { $$typeof: l, type: a.type, key: b, ref: a.ref, props: a.props, _owner: a._owner };
    }
    function O(a) {
      return "object" === typeof a && null !== a && a.$$typeof === l;
    }
    function escape(a) {
      var b = { "=": "=0", ":": "=2" };
      return "$" + a.replace(/[=:]/g, function(a2) {
        return b[a2];
      });
    }
    var P = /\/+/g;
    function Q(a, b) {
      return "object" === typeof a && null !== a && null != a.key ? escape("" + a.key) : b.toString(36);
    }
    function R(a, b, e, d, c) {
      var k = typeof a;
      if ("undefined" === k || "boolean" === k) a = null;
      var h = false;
      if (null === a) h = true;
      else switch (k) {
        case "string":
        case "number":
          h = true;
          break;
        case "object":
          switch (a.$$typeof) {
            case l:
            case n:
              h = true;
          }
      }
      if (h) return h = a, c = c(h), a = "" === d ? "." + Q(h, 0) : d, I(c) ? (e = "", null != a && (e = a.replace(P, "$&/") + "/"), R(c, b, e, "", function(a2) {
        return a2;
      })) : null != c && (O(c) && (c = N(c, e + (!c.key || h && h.key === c.key ? "" : ("" + c.key).replace(P, "$&/") + "/") + a)), b.push(c)), 1;
      h = 0;
      d = "" === d ? "." : d + ":";
      if (I(a)) for (var g = 0; g < a.length; g++) {
        k = a[g];
        var f = d + Q(k, g);
        h += R(k, b, e, f, c);
      }
      else if (f = A(a), "function" === typeof f) for (a = f.call(a), g = 0; !(k = a.next()).done; ) k = k.value, f = d + Q(k, g++), h += R(k, b, e, f, c);
      else if ("object" === k) throw b = String(a), Error("Objects are not valid as a React child (found: " + ("[object Object]" === b ? "object with keys {" + Object.keys(a).join(", ") + "}" : b) + "). If you meant to render a collection of children, use an array instead.");
      return h;
    }
    function S(a, b, e) {
      if (null == a) return a;
      var d = [], c = 0;
      R(a, d, "", "", function(a2) {
        return b.call(e, a2, c++);
      });
      return d;
    }
    function T(a) {
      if (-1 === a._status) {
        var b = a._result;
        b = b();
        b.then(function(b2) {
          if (0 === a._status || -1 === a._status) a._status = 1, a._result = b2;
        }, function(b2) {
          if (0 === a._status || -1 === a._status) a._status = 2, a._result = b2;
        });
        -1 === a._status && (a._status = 0, a._result = b);
      }
      if (1 === a._status) return a._result.default;
      throw a._result;
    }
    var U = { current: null }, V = { transition: null }, W = { ReactCurrentDispatcher: U, ReactCurrentBatchConfig: V, ReactCurrentOwner: K };
    function X() {
      throw Error("act(...) is not supported in production builds of React.");
    }
    react_production_min.Children = { map: S, forEach: function(a, b, e) {
      S(a, function() {
        b.apply(this, arguments);
      }, e);
    }, count: function(a) {
      var b = 0;
      S(a, function() {
        b++;
      });
      return b;
    }, toArray: function(a) {
      return S(a, function(a2) {
        return a2;
      }) || [];
    }, only: function(a) {
      if (!O(a)) throw Error("React.Children.only expected to receive a single React element child.");
      return a;
    } };
    react_production_min.Component = E;
    react_production_min.Fragment = p;
    react_production_min.Profiler = r;
    react_production_min.PureComponent = G;
    react_production_min.StrictMode = q;
    react_production_min.Suspense = w;
    react_production_min.__SECRET_INTERNALS_DO_NOT_USE_OR_YOU_WILL_BE_FIRED = W;
    react_production_min.act = X;
    react_production_min.cloneElement = function(a, b, e) {
      if (null === a || void 0 === a) throw Error("React.cloneElement(...): The argument must be a React element, but you passed " + a + ".");
      var d = C({}, a.props), c = a.key, k = a.ref, h = a._owner;
      if (null != b) {
        void 0 !== b.ref && (k = b.ref, h = K.current);
        void 0 !== b.key && (c = "" + b.key);
        if (a.type && a.type.defaultProps) var g = a.type.defaultProps;
        for (f in b) J.call(b, f) && !L.hasOwnProperty(f) && (d[f] = void 0 === b[f] && void 0 !== g ? g[f] : b[f]);
      }
      var f = arguments.length - 2;
      if (1 === f) d.children = e;
      else if (1 < f) {
        g = Array(f);
        for (var m = 0; m < f; m++) g[m] = arguments[m + 2];
        d.children = g;
      }
      return { $$typeof: l, type: a.type, key: c, ref: k, props: d, _owner: h };
    };
    react_production_min.createContext = function(a) {
      a = { $$typeof: u, _currentValue: a, _currentValue2: a, _threadCount: 0, Provider: null, Consumer: null, _defaultValue: null, _globalName: null };
      a.Provider = { $$typeof: t, _context: a };
      return a.Consumer = a;
    };
    react_production_min.createElement = M;
    react_production_min.createFactory = function(a) {
      var b = M.bind(null, a);
      b.type = a;
      return b;
    };
    react_production_min.createRef = function() {
      return { current: null };
    };
    react_production_min.forwardRef = function(a) {
      return { $$typeof: v, render: a };
    };
    react_production_min.isValidElement = O;
    react_production_min.lazy = function(a) {
      return { $$typeof: y, _payload: { _status: -1, _result: a }, _init: T };
    };
    react_production_min.memo = function(a, b) {
      return { $$typeof: x, type: a, compare: void 0 === b ? null : b };
    };
    react_production_min.startTransition = function(a) {
      var b = V.transition;
      V.transition = {};
      try {
        a();
      } finally {
        V.transition = b;
      }
    };
    react_production_min.unstable_act = X;
    react_production_min.useCallback = function(a, b) {
      return U.current.useCallback(a, b);
    };
    react_production_min.useContext = function(a) {
      return U.current.useContext(a);
    };
    react_production_min.useDebugValue = function() {
    };
    react_production_min.useDeferredValue = function(a) {
      return U.current.useDeferredValue(a);
    };
    react_production_min.useEffect = function(a, b) {
      return U.current.useEffect(a, b);
    };
    react_production_min.useId = function() {
      return U.current.useId();
    };
    react_production_min.useImperativeHandle = function(a, b, e) {
      return U.current.useImperativeHandle(a, b, e);
    };
    react_production_min.useInsertionEffect = function(a, b) {
      return U.current.useInsertionEffect(a, b);
    };
    react_production_min.useLayoutEffect = function(a, b) {
      return U.current.useLayoutEffect(a, b);
    };
    react_production_min.useMemo = function(a, b) {
      return U.current.useMemo(a, b);
    };
    react_production_min.useReducer = function(a, b, e) {
      return U.current.useReducer(a, b, e);
    };
    react_production_min.useRef = function(a) {
      return U.current.useRef(a);
    };
    react_production_min.useState = function(a) {
      return U.current.useState(a);
    };
    react_production_min.useSyncExternalStore = function(a, b, e) {
      return U.current.useSyncExternalStore(a, b, e);
    };
    react_production_min.useTransition = function() {
      return U.current.useTransition();
    };
    react_production_min.version = "18.3.1";
    return react_production_min;
  }
  var hasRequiredReact;
  function requireReact() {
    if (hasRequiredReact) return react.exports;
    hasRequiredReact = 1;
    {
      react.exports = requireReact_production_min();
    }
    return react.exports;
  }
  /**
   * @license React
   * react-jsx-runtime.production.min.js
   *
   * Copyright (c) Facebook, Inc. and its affiliates.
   *
   * This source code is licensed under the MIT license found in the
   * LICENSE file in the root directory of this source tree.
   */
  var hasRequiredReactJsxRuntime_production_min;
  function requireReactJsxRuntime_production_min() {
    if (hasRequiredReactJsxRuntime_production_min) return reactJsxRuntime_production_min;
    hasRequiredReactJsxRuntime_production_min = 1;
    var f = requireReact(), k = Symbol.for("react.element"), l = Symbol.for("react.fragment"), m = Object.prototype.hasOwnProperty, n = f.__SECRET_INTERNALS_DO_NOT_USE_OR_YOU_WILL_BE_FIRED.ReactCurrentOwner, p = { key: true, ref: true, __self: true, __source: true };
    function q(c, a, g) {
      var b, d = {}, e = null, h = null;
      void 0 !== g && (e = "" + g);
      void 0 !== a.key && (e = "" + a.key);
      void 0 !== a.ref && (h = a.ref);
      for (b in a) m.call(a, b) && !p.hasOwnProperty(b) && (d[b] = a[b]);
      if (c && c.defaultProps) for (b in a = c.defaultProps, a) void 0 === d[b] && (d[b] = a[b]);
      return { $$typeof: k, type: c, key: e, ref: h, props: d, _owner: n.current };
    }
    reactJsxRuntime_production_min.Fragment = l;
    reactJsxRuntime_production_min.jsx = q;
    reactJsxRuntime_production_min.jsxs = q;
    return reactJsxRuntime_production_min;
  }
  var hasRequiredJsxRuntime;
  function requireJsxRuntime() {
    if (hasRequiredJsxRuntime) return jsxRuntime.exports;
    hasRequiredJsxRuntime = 1;
    {
      jsxRuntime.exports = requireReactJsxRuntime_production_min();
    }
    return jsxRuntime.exports;
  }
  var jsxRuntimeExports = requireJsxRuntime();
  var reactDom = { exports: {} };
  var reactDom_production_min = {};
  var scheduler = { exports: {} };
  var scheduler_production_min = {};
  /**
   * @license React
   * scheduler.production.min.js
   *
   * Copyright (c) Facebook, Inc. and its affiliates.
   *
   * This source code is licensed under the MIT license found in the
   * LICENSE file in the root directory of this source tree.
   */
  var hasRequiredScheduler_production_min;
  function requireScheduler_production_min() {
    if (hasRequiredScheduler_production_min) return scheduler_production_min;
    hasRequiredScheduler_production_min = 1;
    (function(exports) {
      function f(a, b) {
        var c = a.length;
        a.push(b);
        a: for (; 0 < c; ) {
          var d = c - 1 >>> 1, e = a[d];
          if (0 < g(e, b)) a[d] = b, a[c] = e, c = d;
          else break a;
        }
      }
      function h(a) {
        return 0 === a.length ? null : a[0];
      }
      function k(a) {
        if (0 === a.length) return null;
        var b = a[0], c = a.pop();
        if (c !== b) {
          a[0] = c;
          a: for (var d = 0, e = a.length, w = e >>> 1; d < w; ) {
            var m = 2 * (d + 1) - 1, C = a[m], n = m + 1, x = a[n];
            if (0 > g(C, c)) n < e && 0 > g(x, C) ? (a[d] = x, a[n] = c, d = n) : (a[d] = C, a[m] = c, d = m);
            else if (n < e && 0 > g(x, c)) a[d] = x, a[n] = c, d = n;
            else break a;
          }
        }
        return b;
      }
      function g(a, b) {
        var c = a.sortIndex - b.sortIndex;
        return 0 !== c ? c : a.id - b.id;
      }
      if ("object" === typeof performance && "function" === typeof performance.now) {
        var l = performance;
        exports.unstable_now = function() {
          return l.now();
        };
      } else {
        var p = Date, q = p.now();
        exports.unstable_now = function() {
          return p.now() - q;
        };
      }
      var r = [], t = [], u = 1, v = null, y = 3, z = false, A = false, B = false, D = "function" === typeof setTimeout ? setTimeout : null, E = "function" === typeof clearTimeout ? clearTimeout : null, F = "undefined" !== typeof setImmediate ? setImmediate : null;
      "undefined" !== typeof navigator && void 0 !== navigator.scheduling && void 0 !== navigator.scheduling.isInputPending && navigator.scheduling.isInputPending.bind(navigator.scheduling);
      function G(a) {
        for (var b = h(t); null !== b; ) {
          if (null === b.callback) k(t);
          else if (b.startTime <= a) k(t), b.sortIndex = b.expirationTime, f(r, b);
          else break;
          b = h(t);
        }
      }
      function H(a) {
        B = false;
        G(a);
        if (!A) if (null !== h(r)) A = true, I(J);
        else {
          var b = h(t);
          null !== b && K(H, b.startTime - a);
        }
      }
      function J(a, b) {
        A = false;
        B && (B = false, E(L), L = -1);
        z = true;
        var c = y;
        try {
          G(b);
          for (v = h(r); null !== v && (!(v.expirationTime > b) || a && !M()); ) {
            var d = v.callback;
            if ("function" === typeof d) {
              v.callback = null;
              y = v.priorityLevel;
              var e = d(v.expirationTime <= b);
              b = exports.unstable_now();
              "function" === typeof e ? v.callback = e : v === h(r) && k(r);
              G(b);
            } else k(r);
            v = h(r);
          }
          if (null !== v) var w = true;
          else {
            var m = h(t);
            null !== m && K(H, m.startTime - b);
            w = false;
          }
          return w;
        } finally {
          v = null, y = c, z = false;
        }
      }
      var N = false, O = null, L = -1, P = 5, Q = -1;
      function M() {
        return exports.unstable_now() - Q < P ? false : true;
      }
      function R() {
        if (null !== O) {
          var a = exports.unstable_now();
          Q = a;
          var b = true;
          try {
            b = O(true, a);
          } finally {
            b ? S() : (N = false, O = null);
          }
        } else N = false;
      }
      var S;
      if ("function" === typeof F) S = function() {
        F(R);
      };
      else if ("undefined" !== typeof MessageChannel) {
        var T = new MessageChannel(), U = T.port2;
        T.port1.onmessage = R;
        S = function() {
          U.postMessage(null);
        };
      } else S = function() {
        D(R, 0);
      };
      function I(a) {
        O = a;
        N || (N = true, S());
      }
      function K(a, b) {
        L = D(function() {
          a(exports.unstable_now());
        }, b);
      }
      exports.unstable_IdlePriority = 5;
      exports.unstable_ImmediatePriority = 1;
      exports.unstable_LowPriority = 4;
      exports.unstable_NormalPriority = 3;
      exports.unstable_Profiling = null;
      exports.unstable_UserBlockingPriority = 2;
      exports.unstable_cancelCallback = function(a) {
        a.callback = null;
      };
      exports.unstable_continueExecution = function() {
        A || z || (A = true, I(J));
      };
      exports.unstable_forceFrameRate = function(a) {
        0 > a || 125 < a ? console.error("forceFrameRate takes a positive int between 0 and 125, forcing frame rates higher than 125 fps is not supported") : P = 0 < a ? Math.floor(1e3 / a) : 5;
      };
      exports.unstable_getCurrentPriorityLevel = function() {
        return y;
      };
      exports.unstable_getFirstCallbackNode = function() {
        return h(r);
      };
      exports.unstable_next = function(a) {
        switch (y) {
          case 1:
          case 2:
          case 3:
            var b = 3;
            break;
          default:
            b = y;
        }
        var c = y;
        y = b;
        try {
          return a();
        } finally {
          y = c;
        }
      };
      exports.unstable_pauseExecution = function() {
      };
      exports.unstable_requestPaint = function() {
      };
      exports.unstable_runWithPriority = function(a, b) {
        switch (a) {
          case 1:
          case 2:
          case 3:
          case 4:
          case 5:
            break;
          default:
            a = 3;
        }
        var c = y;
        y = a;
        try {
          return b();
        } finally {
          y = c;
        }
      };
      exports.unstable_scheduleCallback = function(a, b, c) {
        var d = exports.unstable_now();
        "object" === typeof c && null !== c ? (c = c.delay, c = "number" === typeof c && 0 < c ? d + c : d) : c = d;
        switch (a) {
          case 1:
            var e = -1;
            break;
          case 2:
            e = 250;
            break;
          case 5:
            e = 1073741823;
            break;
          case 4:
            e = 1e4;
            break;
          default:
            e = 5e3;
        }
        e = c + e;
        a = { id: u++, callback: b, priorityLevel: a, startTime: c, expirationTime: e, sortIndex: -1 };
        c > d ? (a.sortIndex = c, f(t, a), null === h(r) && a === h(t) && (B ? (E(L), L = -1) : B = true, K(H, c - d))) : (a.sortIndex = e, f(r, a), A || z || (A = true, I(J)));
        return a;
      };
      exports.unstable_shouldYield = M;
      exports.unstable_wrapCallback = function(a) {
        var b = y;
        return function() {
          var c = y;
          y = b;
          try {
            return a.apply(this, arguments);
          } finally {
            y = c;
          }
        };
      };
    })(scheduler_production_min);
    return scheduler_production_min;
  }
  var hasRequiredScheduler;
  function requireScheduler() {
    if (hasRequiredScheduler) return scheduler.exports;
    hasRequiredScheduler = 1;
    {
      scheduler.exports = requireScheduler_production_min();
    }
    return scheduler.exports;
  }
  /**
   * @license React
   * react-dom.production.min.js
   *
   * Copyright (c) Facebook, Inc. and its affiliates.
   *
   * This source code is licensed under the MIT license found in the
   * LICENSE file in the root directory of this source tree.
   */
  var hasRequiredReactDom_production_min;
  function requireReactDom_production_min() {
    if (hasRequiredReactDom_production_min) return reactDom_production_min;
    hasRequiredReactDom_production_min = 1;
    var aa = requireReact(), ca = requireScheduler();
    function p(a) {
      for (var b = "https://reactjs.org/docs/error-decoder.html?invariant=" + a, c = 1; c < arguments.length; c++) b += "&args[]=" + encodeURIComponent(arguments[c]);
      return "Minified React error #" + a + "; visit " + b + " for the full message or use the non-minified dev environment for full errors and additional helpful warnings.";
    }
    var da = /* @__PURE__ */ new Set(), ea = {};
    function fa(a, b) {
      ha(a, b);
      ha(a + "Capture", b);
    }
    function ha(a, b) {
      ea[a] = b;
      for (a = 0; a < b.length; a++) da.add(b[a]);
    }
    var ia = !("undefined" === typeof window || "undefined" === typeof window.document || "undefined" === typeof window.document.createElement), ja = Object.prototype.hasOwnProperty, ka = /^[:A-Z_a-z\u00C0-\u00D6\u00D8-\u00F6\u00F8-\u02FF\u0370-\u037D\u037F-\u1FFF\u200C-\u200D\u2070-\u218F\u2C00-\u2FEF\u3001-\uD7FF\uF900-\uFDCF\uFDF0-\uFFFD][:A-Z_a-z\u00C0-\u00D6\u00D8-\u00F6\u00F8-\u02FF\u0370-\u037D\u037F-\u1FFF\u200C-\u200D\u2070-\u218F\u2C00-\u2FEF\u3001-\uD7FF\uF900-\uFDCF\uFDF0-\uFFFD\-.0-9\u00B7\u0300-\u036F\u203F-\u2040]*$/, la = {}, ma = {};
    function oa(a) {
      if (ja.call(ma, a)) return true;
      if (ja.call(la, a)) return false;
      if (ka.test(a)) return ma[a] = true;
      la[a] = true;
      return false;
    }
    function pa(a, b, c, d) {
      if (null !== c && 0 === c.type) return false;
      switch (typeof b) {
        case "function":
        case "symbol":
          return true;
        case "boolean":
          if (d) return false;
          if (null !== c) return !c.acceptsBooleans;
          a = a.toLowerCase().slice(0, 5);
          return "data-" !== a && "aria-" !== a;
        default:
          return false;
      }
    }
    function qa(a, b, c, d) {
      if (null === b || "undefined" === typeof b || pa(a, b, c, d)) return true;
      if (d) return false;
      if (null !== c) switch (c.type) {
        case 3:
          return !b;
        case 4:
          return false === b;
        case 5:
          return isNaN(b);
        case 6:
          return isNaN(b) || 1 > b;
      }
      return false;
    }
    function v(a, b, c, d, e, f, g) {
      this.acceptsBooleans = 2 === b || 3 === b || 4 === b;
      this.attributeName = d;
      this.attributeNamespace = e;
      this.mustUseProperty = c;
      this.propertyName = a;
      this.type = b;
      this.sanitizeURL = f;
      this.removeEmptyString = g;
    }
    var z = {};
    "children dangerouslySetInnerHTML defaultValue defaultChecked innerHTML suppressContentEditableWarning suppressHydrationWarning style".split(" ").forEach(function(a) {
      z[a] = new v(a, 0, false, a, null, false, false);
    });
    [["acceptCharset", "accept-charset"], ["className", "class"], ["htmlFor", "for"], ["httpEquiv", "http-equiv"]].forEach(function(a) {
      var b = a[0];
      z[b] = new v(b, 1, false, a[1], null, false, false);
    });
    ["contentEditable", "draggable", "spellCheck", "value"].forEach(function(a) {
      z[a] = new v(a, 2, false, a.toLowerCase(), null, false, false);
    });
    ["autoReverse", "externalResourcesRequired", "focusable", "preserveAlpha"].forEach(function(a) {
      z[a] = new v(a, 2, false, a, null, false, false);
    });
    "allowFullScreen async autoFocus autoPlay controls default defer disabled disablePictureInPicture disableRemotePlayback formNoValidate hidden loop noModule noValidate open playsInline readOnly required reversed scoped seamless itemScope".split(" ").forEach(function(a) {
      z[a] = new v(a, 3, false, a.toLowerCase(), null, false, false);
    });
    ["checked", "multiple", "muted", "selected"].forEach(function(a) {
      z[a] = new v(a, 3, true, a, null, false, false);
    });
    ["capture", "download"].forEach(function(a) {
      z[a] = new v(a, 4, false, a, null, false, false);
    });
    ["cols", "rows", "size", "span"].forEach(function(a) {
      z[a] = new v(a, 6, false, a, null, false, false);
    });
    ["rowSpan", "start"].forEach(function(a) {
      z[a] = new v(a, 5, false, a.toLowerCase(), null, false, false);
    });
    var ra = /[\-:]([a-z])/g;
    function sa(a) {
      return a[1].toUpperCase();
    }
    "accent-height alignment-baseline arabic-form baseline-shift cap-height clip-path clip-rule color-interpolation color-interpolation-filters color-profile color-rendering dominant-baseline enable-background fill-opacity fill-rule flood-color flood-opacity font-family font-size font-size-adjust font-stretch font-style font-variant font-weight glyph-name glyph-orientation-horizontal glyph-orientation-vertical horiz-adv-x horiz-origin-x image-rendering letter-spacing lighting-color marker-end marker-mid marker-start overline-position overline-thickness paint-order panose-1 pointer-events rendering-intent shape-rendering stop-color stop-opacity strikethrough-position strikethrough-thickness stroke-dasharray stroke-dashoffset stroke-linecap stroke-linejoin stroke-miterlimit stroke-opacity stroke-width text-anchor text-decoration text-rendering underline-position underline-thickness unicode-bidi unicode-range units-per-em v-alphabetic v-hanging v-ideographic v-mathematical vector-effect vert-adv-y vert-origin-x vert-origin-y word-spacing writing-mode xmlns:xlink x-height".split(" ").forEach(function(a) {
      var b = a.replace(
        ra,
        sa
      );
      z[b] = new v(b, 1, false, a, null, false, false);
    });
    "xlink:actuate xlink:arcrole xlink:role xlink:show xlink:title xlink:type".split(" ").forEach(function(a) {
      var b = a.replace(ra, sa);
      z[b] = new v(b, 1, false, a, "http://www.w3.org/1999/xlink", false, false);
    });
    ["xml:base", "xml:lang", "xml:space"].forEach(function(a) {
      var b = a.replace(ra, sa);
      z[b] = new v(b, 1, false, a, "http://www.w3.org/XML/1998/namespace", false, false);
    });
    ["tabIndex", "crossOrigin"].forEach(function(a) {
      z[a] = new v(a, 1, false, a.toLowerCase(), null, false, false);
    });
    z.xlinkHref = new v("xlinkHref", 1, false, "xlink:href", "http://www.w3.org/1999/xlink", true, false);
    ["src", "href", "action", "formAction"].forEach(function(a) {
      z[a] = new v(a, 1, false, a.toLowerCase(), null, true, true);
    });
    function ta(a, b, c, d) {
      var e = z.hasOwnProperty(b) ? z[b] : null;
      if (null !== e ? 0 !== e.type : d || !(2 < b.length) || "o" !== b[0] && "O" !== b[0] || "n" !== b[1] && "N" !== b[1]) qa(b, c, e, d) && (c = null), d || null === e ? oa(b) && (null === c ? a.removeAttribute(b) : a.setAttribute(b, "" + c)) : e.mustUseProperty ? a[e.propertyName] = null === c ? 3 === e.type ? false : "" : c : (b = e.attributeName, d = e.attributeNamespace, null === c ? a.removeAttribute(b) : (e = e.type, c = 3 === e || 4 === e && true === c ? "" : "" + c, d ? a.setAttributeNS(d, b, c) : a.setAttribute(b, c)));
    }
    var ua = aa.__SECRET_INTERNALS_DO_NOT_USE_OR_YOU_WILL_BE_FIRED, va = Symbol.for("react.element"), wa = Symbol.for("react.portal"), ya = Symbol.for("react.fragment"), za = Symbol.for("react.strict_mode"), Aa = Symbol.for("react.profiler"), Ba = Symbol.for("react.provider"), Ca = Symbol.for("react.context"), Da = Symbol.for("react.forward_ref"), Ea = Symbol.for("react.suspense"), Fa = Symbol.for("react.suspense_list"), Ga = Symbol.for("react.memo"), Ha = Symbol.for("react.lazy");
    var Ia = Symbol.for("react.offscreen");
    var Ja = Symbol.iterator;
    function Ka(a) {
      if (null === a || "object" !== typeof a) return null;
      a = Ja && a[Ja] || a["@@iterator"];
      return "function" === typeof a ? a : null;
    }
    var A = Object.assign, La;
    function Ma(a) {
      if (void 0 === La) try {
        throw Error();
      } catch (c) {
        var b = c.stack.trim().match(/\n( *(at )?)/);
        La = b && b[1] || "";
      }
      return "\n" + La + a;
    }
    var Na = false;
    function Oa(a, b) {
      if (!a || Na) return "";
      Na = true;
      var c = Error.prepareStackTrace;
      Error.prepareStackTrace = void 0;
      try {
        if (b) if (b = function() {
          throw Error();
        }, Object.defineProperty(b.prototype, "props", { set: function() {
          throw Error();
        } }), "object" === typeof Reflect && Reflect.construct) {
          try {
            Reflect.construct(b, []);
          } catch (l) {
            var d = l;
          }
          Reflect.construct(a, [], b);
        } else {
          try {
            b.call();
          } catch (l) {
            d = l;
          }
          a.call(b.prototype);
        }
        else {
          try {
            throw Error();
          } catch (l) {
            d = l;
          }
          a();
        }
      } catch (l) {
        if (l && d && "string" === typeof l.stack) {
          for (var e = l.stack.split("\n"), f = d.stack.split("\n"), g = e.length - 1, h = f.length - 1; 1 <= g && 0 <= h && e[g] !== f[h]; ) h--;
          for (; 1 <= g && 0 <= h; g--, h--) if (e[g] !== f[h]) {
            if (1 !== g || 1 !== h) {
              do
                if (g--, h--, 0 > h || e[g] !== f[h]) {
                  var k = "\n" + e[g].replace(" at new ", " at ");
                  a.displayName && k.includes("<anonymous>") && (k = k.replace("<anonymous>", a.displayName));
                  return k;
                }
              while (1 <= g && 0 <= h);
            }
            break;
          }
        }
      } finally {
        Na = false, Error.prepareStackTrace = c;
      }
      return (a = a ? a.displayName || a.name : "") ? Ma(a) : "";
    }
    function Pa(a) {
      switch (a.tag) {
        case 5:
          return Ma(a.type);
        case 16:
          return Ma("Lazy");
        case 13:
          return Ma("Suspense");
        case 19:
          return Ma("SuspenseList");
        case 0:
        case 2:
        case 15:
          return a = Oa(a.type, false), a;
        case 11:
          return a = Oa(a.type.render, false), a;
        case 1:
          return a = Oa(a.type, true), a;
        default:
          return "";
      }
    }
    function Qa(a) {
      if (null == a) return null;
      if ("function" === typeof a) return a.displayName || a.name || null;
      if ("string" === typeof a) return a;
      switch (a) {
        case ya:
          return "Fragment";
        case wa:
          return "Portal";
        case Aa:
          return "Profiler";
        case za:
          return "StrictMode";
        case Ea:
          return "Suspense";
        case Fa:
          return "SuspenseList";
      }
      if ("object" === typeof a) switch (a.$$typeof) {
        case Ca:
          return (a.displayName || "Context") + ".Consumer";
        case Ba:
          return (a._context.displayName || "Context") + ".Provider";
        case Da:
          var b = a.render;
          a = a.displayName;
          a || (a = b.displayName || b.name || "", a = "" !== a ? "ForwardRef(" + a + ")" : "ForwardRef");
          return a;
        case Ga:
          return b = a.displayName || null, null !== b ? b : Qa(a.type) || "Memo";
        case Ha:
          b = a._payload;
          a = a._init;
          try {
            return Qa(a(b));
          } catch (c) {
          }
      }
      return null;
    }
    function Ra(a) {
      var b = a.type;
      switch (a.tag) {
        case 24:
          return "Cache";
        case 9:
          return (b.displayName || "Context") + ".Consumer";
        case 10:
          return (b._context.displayName || "Context") + ".Provider";
        case 18:
          return "DehydratedFragment";
        case 11:
          return a = b.render, a = a.displayName || a.name || "", b.displayName || ("" !== a ? "ForwardRef(" + a + ")" : "ForwardRef");
        case 7:
          return "Fragment";
        case 5:
          return b;
        case 4:
          return "Portal";
        case 3:
          return "Root";
        case 6:
          return "Text";
        case 16:
          return Qa(b);
        case 8:
          return b === za ? "StrictMode" : "Mode";
        case 22:
          return "Offscreen";
        case 12:
          return "Profiler";
        case 21:
          return "Scope";
        case 13:
          return "Suspense";
        case 19:
          return "SuspenseList";
        case 25:
          return "TracingMarker";
        case 1:
        case 0:
        case 17:
        case 2:
        case 14:
        case 15:
          if ("function" === typeof b) return b.displayName || b.name || null;
          if ("string" === typeof b) return b;
      }
      return null;
    }
    function Sa(a) {
      switch (typeof a) {
        case "boolean":
        case "number":
        case "string":
        case "undefined":
          return a;
        case "object":
          return a;
        default:
          return "";
      }
    }
    function Ta(a) {
      var b = a.type;
      return (a = a.nodeName) && "input" === a.toLowerCase() && ("checkbox" === b || "radio" === b);
    }
    function Ua(a) {
      var b = Ta(a) ? "checked" : "value", c = Object.getOwnPropertyDescriptor(a.constructor.prototype, b), d = "" + a[b];
      if (!a.hasOwnProperty(b) && "undefined" !== typeof c && "function" === typeof c.get && "function" === typeof c.set) {
        var e = c.get, f = c.set;
        Object.defineProperty(a, b, { configurable: true, get: function() {
          return e.call(this);
        }, set: function(a2) {
          d = "" + a2;
          f.call(this, a2);
        } });
        Object.defineProperty(a, b, { enumerable: c.enumerable });
        return { getValue: function() {
          return d;
        }, setValue: function(a2) {
          d = "" + a2;
        }, stopTracking: function() {
          a._valueTracker = null;
          delete a[b];
        } };
      }
    }
    function Va(a) {
      a._valueTracker || (a._valueTracker = Ua(a));
    }
    function Wa(a) {
      if (!a) return false;
      var b = a._valueTracker;
      if (!b) return true;
      var c = b.getValue();
      var d = "";
      a && (d = Ta(a) ? a.checked ? "true" : "false" : a.value);
      a = d;
      return a !== c ? (b.setValue(a), true) : false;
    }
    function Xa(a) {
      a = a || ("undefined" !== typeof document ? document : void 0);
      if ("undefined" === typeof a) return null;
      try {
        return a.activeElement || a.body;
      } catch (b) {
        return a.body;
      }
    }
    function Ya(a, b) {
      var c = b.checked;
      return A({}, b, { defaultChecked: void 0, defaultValue: void 0, value: void 0, checked: null != c ? c : a._wrapperState.initialChecked });
    }
    function Za(a, b) {
      var c = null == b.defaultValue ? "" : b.defaultValue, d = null != b.checked ? b.checked : b.defaultChecked;
      c = Sa(null != b.value ? b.value : c);
      a._wrapperState = { initialChecked: d, initialValue: c, controlled: "checkbox" === b.type || "radio" === b.type ? null != b.checked : null != b.value };
    }
    function ab(a, b) {
      b = b.checked;
      null != b && ta(a, "checked", b, false);
    }
    function bb(a, b) {
      ab(a, b);
      var c = Sa(b.value), d = b.type;
      if (null != c) if ("number" === d) {
        if (0 === c && "" === a.value || a.value != c) a.value = "" + c;
      } else a.value !== "" + c && (a.value = "" + c);
      else if ("submit" === d || "reset" === d) {
        a.removeAttribute("value");
        return;
      }
      b.hasOwnProperty("value") ? cb(a, b.type, c) : b.hasOwnProperty("defaultValue") && cb(a, b.type, Sa(b.defaultValue));
      null == b.checked && null != b.defaultChecked && (a.defaultChecked = !!b.defaultChecked);
    }
    function db(a, b, c) {
      if (b.hasOwnProperty("value") || b.hasOwnProperty("defaultValue")) {
        var d = b.type;
        if (!("submit" !== d && "reset" !== d || void 0 !== b.value && null !== b.value)) return;
        b = "" + a._wrapperState.initialValue;
        c || b === a.value || (a.value = b);
        a.defaultValue = b;
      }
      c = a.name;
      "" !== c && (a.name = "");
      a.defaultChecked = !!a._wrapperState.initialChecked;
      "" !== c && (a.name = c);
    }
    function cb(a, b, c) {
      if ("number" !== b || Xa(a.ownerDocument) !== a) null == c ? a.defaultValue = "" + a._wrapperState.initialValue : a.defaultValue !== "" + c && (a.defaultValue = "" + c);
    }
    var eb = Array.isArray;
    function fb(a, b, c, d) {
      a = a.options;
      if (b) {
        b = {};
        for (var e = 0; e < c.length; e++) b["$" + c[e]] = true;
        for (c = 0; c < a.length; c++) e = b.hasOwnProperty("$" + a[c].value), a[c].selected !== e && (a[c].selected = e), e && d && (a[c].defaultSelected = true);
      } else {
        c = "" + Sa(c);
        b = null;
        for (e = 0; e < a.length; e++) {
          if (a[e].value === c) {
            a[e].selected = true;
            d && (a[e].defaultSelected = true);
            return;
          }
          null !== b || a[e].disabled || (b = a[e]);
        }
        null !== b && (b.selected = true);
      }
    }
    function gb(a, b) {
      if (null != b.dangerouslySetInnerHTML) throw Error(p(91));
      return A({}, b, { value: void 0, defaultValue: void 0, children: "" + a._wrapperState.initialValue });
    }
    function hb(a, b) {
      var c = b.value;
      if (null == c) {
        c = b.children;
        b = b.defaultValue;
        if (null != c) {
          if (null != b) throw Error(p(92));
          if (eb(c)) {
            if (1 < c.length) throw Error(p(93));
            c = c[0];
          }
          b = c;
        }
        null == b && (b = "");
        c = b;
      }
      a._wrapperState = { initialValue: Sa(c) };
    }
    function ib(a, b) {
      var c = Sa(b.value), d = Sa(b.defaultValue);
      null != c && (c = "" + c, c !== a.value && (a.value = c), null == b.defaultValue && a.defaultValue !== c && (a.defaultValue = c));
      null != d && (a.defaultValue = "" + d);
    }
    function jb(a) {
      var b = a.textContent;
      b === a._wrapperState.initialValue && "" !== b && null !== b && (a.value = b);
    }
    function kb(a) {
      switch (a) {
        case "svg":
          return "http://www.w3.org/2000/svg";
        case "math":
          return "http://www.w3.org/1998/Math/MathML";
        default:
          return "http://www.w3.org/1999/xhtml";
      }
    }
    function lb(a, b) {
      return null == a || "http://www.w3.org/1999/xhtml" === a ? kb(b) : "http://www.w3.org/2000/svg" === a && "foreignObject" === b ? "http://www.w3.org/1999/xhtml" : a;
    }
    var mb, nb = function(a) {
      return "undefined" !== typeof MSApp && MSApp.execUnsafeLocalFunction ? function(b, c, d, e) {
        MSApp.execUnsafeLocalFunction(function() {
          return a(b, c, d, e);
        });
      } : a;
    }(function(a, b) {
      if ("http://www.w3.org/2000/svg" !== a.namespaceURI || "innerHTML" in a) a.innerHTML = b;
      else {
        mb = mb || document.createElement("div");
        mb.innerHTML = "<svg>" + b.valueOf().toString() + "</svg>";
        for (b = mb.firstChild; a.firstChild; ) a.removeChild(a.firstChild);
        for (; b.firstChild; ) a.appendChild(b.firstChild);
      }
    });
    function ob(a, b) {
      if (b) {
        var c = a.firstChild;
        if (c && c === a.lastChild && 3 === c.nodeType) {
          c.nodeValue = b;
          return;
        }
      }
      a.textContent = b;
    }
    var pb = {
      animationIterationCount: true,
      aspectRatio: true,
      borderImageOutset: true,
      borderImageSlice: true,
      borderImageWidth: true,
      boxFlex: true,
      boxFlexGroup: true,
      boxOrdinalGroup: true,
      columnCount: true,
      columns: true,
      flex: true,
      flexGrow: true,
      flexPositive: true,
      flexShrink: true,
      flexNegative: true,
      flexOrder: true,
      gridArea: true,
      gridRow: true,
      gridRowEnd: true,
      gridRowSpan: true,
      gridRowStart: true,
      gridColumn: true,
      gridColumnEnd: true,
      gridColumnSpan: true,
      gridColumnStart: true,
      fontWeight: true,
      lineClamp: true,
      lineHeight: true,
      opacity: true,
      order: true,
      orphans: true,
      tabSize: true,
      widows: true,
      zIndex: true,
      zoom: true,
      fillOpacity: true,
      floodOpacity: true,
      stopOpacity: true,
      strokeDasharray: true,
      strokeDashoffset: true,
      strokeMiterlimit: true,
      strokeOpacity: true,
      strokeWidth: true
    }, qb = ["Webkit", "ms", "Moz", "O"];
    Object.keys(pb).forEach(function(a) {
      qb.forEach(function(b) {
        b = b + a.charAt(0).toUpperCase() + a.substring(1);
        pb[b] = pb[a];
      });
    });
    function rb(a, b, c) {
      return null == b || "boolean" === typeof b || "" === b ? "" : c || "number" !== typeof b || 0 === b || pb.hasOwnProperty(a) && pb[a] ? ("" + b).trim() : b + "px";
    }
    function sb(a, b) {
      a = a.style;
      for (var c in b) if (b.hasOwnProperty(c)) {
        var d = 0 === c.indexOf("--"), e = rb(c, b[c], d);
        "float" === c && (c = "cssFloat");
        d ? a.setProperty(c, e) : a[c] = e;
      }
    }
    var tb = A({ menuitem: true }, { area: true, base: true, br: true, col: true, embed: true, hr: true, img: true, input: true, keygen: true, link: true, meta: true, param: true, source: true, track: true, wbr: true });
    function ub(a, b) {
      if (b) {
        if (tb[a] && (null != b.children || null != b.dangerouslySetInnerHTML)) throw Error(p(137, a));
        if (null != b.dangerouslySetInnerHTML) {
          if (null != b.children) throw Error(p(60));
          if ("object" !== typeof b.dangerouslySetInnerHTML || !("__html" in b.dangerouslySetInnerHTML)) throw Error(p(61));
        }
        if (null != b.style && "object" !== typeof b.style) throw Error(p(62));
      }
    }
    function vb(a, b) {
      if (-1 === a.indexOf("-")) return "string" === typeof b.is;
      switch (a) {
        case "annotation-xml":
        case "color-profile":
        case "font-face":
        case "font-face-src":
        case "font-face-uri":
        case "font-face-format":
        case "font-face-name":
        case "missing-glyph":
          return false;
        default:
          return true;
      }
    }
    var wb = null;
    function xb(a) {
      a = a.target || a.srcElement || window;
      a.correspondingUseElement && (a = a.correspondingUseElement);
      return 3 === a.nodeType ? a.parentNode : a;
    }
    var yb = null, zb = null, Ab = null;
    function Bb(a) {
      if (a = Cb(a)) {
        if ("function" !== typeof yb) throw Error(p(280));
        var b = a.stateNode;
        b && (b = Db(b), yb(a.stateNode, a.type, b));
      }
    }
    function Eb(a) {
      zb ? Ab ? Ab.push(a) : Ab = [a] : zb = a;
    }
    function Fb() {
      if (zb) {
        var a = zb, b = Ab;
        Ab = zb = null;
        Bb(a);
        if (b) for (a = 0; a < b.length; a++) Bb(b[a]);
      }
    }
    function Gb(a, b) {
      return a(b);
    }
    function Hb() {
    }
    var Ib = false;
    function Jb(a, b, c) {
      if (Ib) return a(b, c);
      Ib = true;
      try {
        return Gb(a, b, c);
      } finally {
        if (Ib = false, null !== zb || null !== Ab) Hb(), Fb();
      }
    }
    function Kb(a, b) {
      var c = a.stateNode;
      if (null === c) return null;
      var d = Db(c);
      if (null === d) return null;
      c = d[b];
      a: switch (b) {
        case "onClick":
        case "onClickCapture":
        case "onDoubleClick":
        case "onDoubleClickCapture":
        case "onMouseDown":
        case "onMouseDownCapture":
        case "onMouseMove":
        case "onMouseMoveCapture":
        case "onMouseUp":
        case "onMouseUpCapture":
        case "onMouseEnter":
          (d = !d.disabled) || (a = a.type, d = !("button" === a || "input" === a || "select" === a || "textarea" === a));
          a = !d;
          break a;
        default:
          a = false;
      }
      if (a) return null;
      if (c && "function" !== typeof c) throw Error(p(231, b, typeof c));
      return c;
    }
    var Lb = false;
    if (ia) try {
      var Mb = {};
      Object.defineProperty(Mb, "passive", { get: function() {
        Lb = true;
      } });
      window.addEventListener("test", Mb, Mb);
      window.removeEventListener("test", Mb, Mb);
    } catch (a) {
      Lb = false;
    }
    function Nb(a, b, c, d, e, f, g, h, k) {
      var l = Array.prototype.slice.call(arguments, 3);
      try {
        b.apply(c, l);
      } catch (m) {
        this.onError(m);
      }
    }
    var Ob = false, Pb = null, Qb = false, Rb = null, Sb = { onError: function(a) {
      Ob = true;
      Pb = a;
    } };
    function Tb(a, b, c, d, e, f, g, h, k) {
      Ob = false;
      Pb = null;
      Nb.apply(Sb, arguments);
    }
    function Ub(a, b, c, d, e, f, g, h, k) {
      Tb.apply(this, arguments);
      if (Ob) {
        if (Ob) {
          var l = Pb;
          Ob = false;
          Pb = null;
        } else throw Error(p(198));
        Qb || (Qb = true, Rb = l);
      }
    }
    function Vb(a) {
      var b = a, c = a;
      if (a.alternate) for (; b.return; ) b = b.return;
      else {
        a = b;
        do
          b = a, 0 !== (b.flags & 4098) && (c = b.return), a = b.return;
        while (a);
      }
      return 3 === b.tag ? c : null;
    }
    function Wb(a) {
      if (13 === a.tag) {
        var b = a.memoizedState;
        null === b && (a = a.alternate, null !== a && (b = a.memoizedState));
        if (null !== b) return b.dehydrated;
      }
      return null;
    }
    function Xb(a) {
      if (Vb(a) !== a) throw Error(p(188));
    }
    function Yb(a) {
      var b = a.alternate;
      if (!b) {
        b = Vb(a);
        if (null === b) throw Error(p(188));
        return b !== a ? null : a;
      }
      for (var c = a, d = b; ; ) {
        var e = c.return;
        if (null === e) break;
        var f = e.alternate;
        if (null === f) {
          d = e.return;
          if (null !== d) {
            c = d;
            continue;
          }
          break;
        }
        if (e.child === f.child) {
          for (f = e.child; f; ) {
            if (f === c) return Xb(e), a;
            if (f === d) return Xb(e), b;
            f = f.sibling;
          }
          throw Error(p(188));
        }
        if (c.return !== d.return) c = e, d = f;
        else {
          for (var g = false, h = e.child; h; ) {
            if (h === c) {
              g = true;
              c = e;
              d = f;
              break;
            }
            if (h === d) {
              g = true;
              d = e;
              c = f;
              break;
            }
            h = h.sibling;
          }
          if (!g) {
            for (h = f.child; h; ) {
              if (h === c) {
                g = true;
                c = f;
                d = e;
                break;
              }
              if (h === d) {
                g = true;
                d = f;
                c = e;
                break;
              }
              h = h.sibling;
            }
            if (!g) throw Error(p(189));
          }
        }
        if (c.alternate !== d) throw Error(p(190));
      }
      if (3 !== c.tag) throw Error(p(188));
      return c.stateNode.current === c ? a : b;
    }
    function Zb(a) {
      a = Yb(a);
      return null !== a ? $b(a) : null;
    }
    function $b(a) {
      if (5 === a.tag || 6 === a.tag) return a;
      for (a = a.child; null !== a; ) {
        var b = $b(a);
        if (null !== b) return b;
        a = a.sibling;
      }
      return null;
    }
    var ac = ca.unstable_scheduleCallback, bc = ca.unstable_cancelCallback, cc = ca.unstable_shouldYield, dc = ca.unstable_requestPaint, B = ca.unstable_now, ec = ca.unstable_getCurrentPriorityLevel, fc = ca.unstable_ImmediatePriority, gc = ca.unstable_UserBlockingPriority, hc = ca.unstable_NormalPriority, ic = ca.unstable_LowPriority, jc = ca.unstable_IdlePriority, kc = null, lc = null;
    function mc(a) {
      if (lc && "function" === typeof lc.onCommitFiberRoot) try {
        lc.onCommitFiberRoot(kc, a, void 0, 128 === (a.current.flags & 128));
      } catch (b) {
      }
    }
    var oc = Math.clz32 ? Math.clz32 : nc, pc = Math.log, qc = Math.LN2;
    function nc(a) {
      a >>>= 0;
      return 0 === a ? 32 : 31 - (pc(a) / qc | 0) | 0;
    }
    var rc = 64, sc = 4194304;
    function tc(a) {
      switch (a & -a) {
        case 1:
          return 1;
        case 2:
          return 2;
        case 4:
          return 4;
        case 8:
          return 8;
        case 16:
          return 16;
        case 32:
          return 32;
        case 64:
        case 128:
        case 256:
        case 512:
        case 1024:
        case 2048:
        case 4096:
        case 8192:
        case 16384:
        case 32768:
        case 65536:
        case 131072:
        case 262144:
        case 524288:
        case 1048576:
        case 2097152:
          return a & 4194240;
        case 4194304:
        case 8388608:
        case 16777216:
        case 33554432:
        case 67108864:
          return a & 130023424;
        case 134217728:
          return 134217728;
        case 268435456:
          return 268435456;
        case 536870912:
          return 536870912;
        case 1073741824:
          return 1073741824;
        default:
          return a;
      }
    }
    function uc(a, b) {
      var c = a.pendingLanes;
      if (0 === c) return 0;
      var d = 0, e = a.suspendedLanes, f = a.pingedLanes, g = c & 268435455;
      if (0 !== g) {
        var h = g & ~e;
        0 !== h ? d = tc(h) : (f &= g, 0 !== f && (d = tc(f)));
      } else g = c & ~e, 0 !== g ? d = tc(g) : 0 !== f && (d = tc(f));
      if (0 === d) return 0;
      if (0 !== b && b !== d && 0 === (b & e) && (e = d & -d, f = b & -b, e >= f || 16 === e && 0 !== (f & 4194240))) return b;
      0 !== (d & 4) && (d |= c & 16);
      b = a.entangledLanes;
      if (0 !== b) for (a = a.entanglements, b &= d; 0 < b; ) c = 31 - oc(b), e = 1 << c, d |= a[c], b &= ~e;
      return d;
    }
    function vc(a, b) {
      switch (a) {
        case 1:
        case 2:
        case 4:
          return b + 250;
        case 8:
        case 16:
        case 32:
        case 64:
        case 128:
        case 256:
        case 512:
        case 1024:
        case 2048:
        case 4096:
        case 8192:
        case 16384:
        case 32768:
        case 65536:
        case 131072:
        case 262144:
        case 524288:
        case 1048576:
        case 2097152:
          return b + 5e3;
        case 4194304:
        case 8388608:
        case 16777216:
        case 33554432:
        case 67108864:
          return -1;
        case 134217728:
        case 268435456:
        case 536870912:
        case 1073741824:
          return -1;
        default:
          return -1;
      }
    }
    function wc(a, b) {
      for (var c = a.suspendedLanes, d = a.pingedLanes, e = a.expirationTimes, f = a.pendingLanes; 0 < f; ) {
        var g = 31 - oc(f), h = 1 << g, k = e[g];
        if (-1 === k) {
          if (0 === (h & c) || 0 !== (h & d)) e[g] = vc(h, b);
        } else k <= b && (a.expiredLanes |= h);
        f &= ~h;
      }
    }
    function xc(a) {
      a = a.pendingLanes & -1073741825;
      return 0 !== a ? a : a & 1073741824 ? 1073741824 : 0;
    }
    function yc() {
      var a = rc;
      rc <<= 1;
      0 === (rc & 4194240) && (rc = 64);
      return a;
    }
    function zc(a) {
      for (var b = [], c = 0; 31 > c; c++) b.push(a);
      return b;
    }
    function Ac(a, b, c) {
      a.pendingLanes |= b;
      536870912 !== b && (a.suspendedLanes = 0, a.pingedLanes = 0);
      a = a.eventTimes;
      b = 31 - oc(b);
      a[b] = c;
    }
    function Bc(a, b) {
      var c = a.pendingLanes & ~b;
      a.pendingLanes = b;
      a.suspendedLanes = 0;
      a.pingedLanes = 0;
      a.expiredLanes &= b;
      a.mutableReadLanes &= b;
      a.entangledLanes &= b;
      b = a.entanglements;
      var d = a.eventTimes;
      for (a = a.expirationTimes; 0 < c; ) {
        var e = 31 - oc(c), f = 1 << e;
        b[e] = 0;
        d[e] = -1;
        a[e] = -1;
        c &= ~f;
      }
    }
    function Cc(a, b) {
      var c = a.entangledLanes |= b;
      for (a = a.entanglements; c; ) {
        var d = 31 - oc(c), e = 1 << d;
        e & b | a[d] & b && (a[d] |= b);
        c &= ~e;
      }
    }
    var C = 0;
    function Dc(a) {
      a &= -a;
      return 1 < a ? 4 < a ? 0 !== (a & 268435455) ? 16 : 536870912 : 4 : 1;
    }
    var Ec, Fc, Gc, Hc, Ic, Jc = false, Kc = [], Lc = null, Mc = null, Nc = null, Oc = /* @__PURE__ */ new Map(), Pc = /* @__PURE__ */ new Map(), Qc = [], Rc = "mousedown mouseup touchcancel touchend touchstart auxclick dblclick pointercancel pointerdown pointerup dragend dragstart drop compositionend compositionstart keydown keypress keyup input textInput copy cut paste click change contextmenu reset submit".split(" ");
    function Sc(a, b) {
      switch (a) {
        case "focusin":
        case "focusout":
          Lc = null;
          break;
        case "dragenter":
        case "dragleave":
          Mc = null;
          break;
        case "mouseover":
        case "mouseout":
          Nc = null;
          break;
        case "pointerover":
        case "pointerout":
          Oc.delete(b.pointerId);
          break;
        case "gotpointercapture":
        case "lostpointercapture":
          Pc.delete(b.pointerId);
      }
    }
    function Tc(a, b, c, d, e, f) {
      if (null === a || a.nativeEvent !== f) return a = { blockedOn: b, domEventName: c, eventSystemFlags: d, nativeEvent: f, targetContainers: [e] }, null !== b && (b = Cb(b), null !== b && Fc(b)), a;
      a.eventSystemFlags |= d;
      b = a.targetContainers;
      null !== e && -1 === b.indexOf(e) && b.push(e);
      return a;
    }
    function Uc(a, b, c, d, e) {
      switch (b) {
        case "focusin":
          return Lc = Tc(Lc, a, b, c, d, e), true;
        case "dragenter":
          return Mc = Tc(Mc, a, b, c, d, e), true;
        case "mouseover":
          return Nc = Tc(Nc, a, b, c, d, e), true;
        case "pointerover":
          var f = e.pointerId;
          Oc.set(f, Tc(Oc.get(f) || null, a, b, c, d, e));
          return true;
        case "gotpointercapture":
          return f = e.pointerId, Pc.set(f, Tc(Pc.get(f) || null, a, b, c, d, e)), true;
      }
      return false;
    }
    function Vc(a) {
      var b = Wc(a.target);
      if (null !== b) {
        var c = Vb(b);
        if (null !== c) {
          if (b = c.tag, 13 === b) {
            if (b = Wb(c), null !== b) {
              a.blockedOn = b;
              Ic(a.priority, function() {
                Gc(c);
              });
              return;
            }
          } else if (3 === b && c.stateNode.current.memoizedState.isDehydrated) {
            a.blockedOn = 3 === c.tag ? c.stateNode.containerInfo : null;
            return;
          }
        }
      }
      a.blockedOn = null;
    }
    function Xc(a) {
      if (null !== a.blockedOn) return false;
      for (var b = a.targetContainers; 0 < b.length; ) {
        var c = Yc(a.domEventName, a.eventSystemFlags, b[0], a.nativeEvent);
        if (null === c) {
          c = a.nativeEvent;
          var d = new c.constructor(c.type, c);
          wb = d;
          c.target.dispatchEvent(d);
          wb = null;
        } else return b = Cb(c), null !== b && Fc(b), a.blockedOn = c, false;
        b.shift();
      }
      return true;
    }
    function Zc(a, b, c) {
      Xc(a) && c.delete(b);
    }
    function $c() {
      Jc = false;
      null !== Lc && Xc(Lc) && (Lc = null);
      null !== Mc && Xc(Mc) && (Mc = null);
      null !== Nc && Xc(Nc) && (Nc = null);
      Oc.forEach(Zc);
      Pc.forEach(Zc);
    }
    function ad(a, b) {
      a.blockedOn === b && (a.blockedOn = null, Jc || (Jc = true, ca.unstable_scheduleCallback(ca.unstable_NormalPriority, $c)));
    }
    function bd(a) {
      function b(b2) {
        return ad(b2, a);
      }
      if (0 < Kc.length) {
        ad(Kc[0], a);
        for (var c = 1; c < Kc.length; c++) {
          var d = Kc[c];
          d.blockedOn === a && (d.blockedOn = null);
        }
      }
      null !== Lc && ad(Lc, a);
      null !== Mc && ad(Mc, a);
      null !== Nc && ad(Nc, a);
      Oc.forEach(b);
      Pc.forEach(b);
      for (c = 0; c < Qc.length; c++) d = Qc[c], d.blockedOn === a && (d.blockedOn = null);
      for (; 0 < Qc.length && (c = Qc[0], null === c.blockedOn); ) Vc(c), null === c.blockedOn && Qc.shift();
    }
    var cd = ua.ReactCurrentBatchConfig, dd = true;
    function ed(a, b, c, d) {
      var e = C, f = cd.transition;
      cd.transition = null;
      try {
        C = 1, fd(a, b, c, d);
      } finally {
        C = e, cd.transition = f;
      }
    }
    function gd(a, b, c, d) {
      var e = C, f = cd.transition;
      cd.transition = null;
      try {
        C = 4, fd(a, b, c, d);
      } finally {
        C = e, cd.transition = f;
      }
    }
    function fd(a, b, c, d) {
      if (dd) {
        var e = Yc(a, b, c, d);
        if (null === e) hd(a, b, d, id, c), Sc(a, d);
        else if (Uc(e, a, b, c, d)) d.stopPropagation();
        else if (Sc(a, d), b & 4 && -1 < Rc.indexOf(a)) {
          for (; null !== e; ) {
            var f = Cb(e);
            null !== f && Ec(f);
            f = Yc(a, b, c, d);
            null === f && hd(a, b, d, id, c);
            if (f === e) break;
            e = f;
          }
          null !== e && d.stopPropagation();
        } else hd(a, b, d, null, c);
      }
    }
    var id = null;
    function Yc(a, b, c, d) {
      id = null;
      a = xb(d);
      a = Wc(a);
      if (null !== a) if (b = Vb(a), null === b) a = null;
      else if (c = b.tag, 13 === c) {
        a = Wb(b);
        if (null !== a) return a;
        a = null;
      } else if (3 === c) {
        if (b.stateNode.current.memoizedState.isDehydrated) return 3 === b.tag ? b.stateNode.containerInfo : null;
        a = null;
      } else b !== a && (a = null);
      id = a;
      return null;
    }
    function jd(a) {
      switch (a) {
        case "cancel":
        case "click":
        case "close":
        case "contextmenu":
        case "copy":
        case "cut":
        case "auxclick":
        case "dblclick":
        case "dragend":
        case "dragstart":
        case "drop":
        case "focusin":
        case "focusout":
        case "input":
        case "invalid":
        case "keydown":
        case "keypress":
        case "keyup":
        case "mousedown":
        case "mouseup":
        case "paste":
        case "pause":
        case "play":
        case "pointercancel":
        case "pointerdown":
        case "pointerup":
        case "ratechange":
        case "reset":
        case "resize":
        case "seeked":
        case "submit":
        case "touchcancel":
        case "touchend":
        case "touchstart":
        case "volumechange":
        case "change":
        case "selectionchange":
        case "textInput":
        case "compositionstart":
        case "compositionend":
        case "compositionupdate":
        case "beforeblur":
        case "afterblur":
        case "beforeinput":
        case "blur":
        case "fullscreenchange":
        case "focus":
        case "hashchange":
        case "popstate":
        case "select":
        case "selectstart":
          return 1;
        case "drag":
        case "dragenter":
        case "dragexit":
        case "dragleave":
        case "dragover":
        case "mousemove":
        case "mouseout":
        case "mouseover":
        case "pointermove":
        case "pointerout":
        case "pointerover":
        case "scroll":
        case "toggle":
        case "touchmove":
        case "wheel":
        case "mouseenter":
        case "mouseleave":
        case "pointerenter":
        case "pointerleave":
          return 4;
        case "message":
          switch (ec()) {
            case fc:
              return 1;
            case gc:
              return 4;
            case hc:
            case ic:
              return 16;
            case jc:
              return 536870912;
            default:
              return 16;
          }
        default:
          return 16;
      }
    }
    var kd = null, ld = null, md = null;
    function nd() {
      if (md) return md;
      var a, b = ld, c = b.length, d, e = "value" in kd ? kd.value : kd.textContent, f = e.length;
      for (a = 0; a < c && b[a] === e[a]; a++) ;
      var g = c - a;
      for (d = 1; d <= g && b[c - d] === e[f - d]; d++) ;
      return md = e.slice(a, 1 < d ? 1 - d : void 0);
    }
    function od(a) {
      var b = a.keyCode;
      "charCode" in a ? (a = a.charCode, 0 === a && 13 === b && (a = 13)) : a = b;
      10 === a && (a = 13);
      return 32 <= a || 13 === a ? a : 0;
    }
    function pd() {
      return true;
    }
    function qd() {
      return false;
    }
    function rd(a) {
      function b(b2, d, e, f, g) {
        this._reactName = b2;
        this._targetInst = e;
        this.type = d;
        this.nativeEvent = f;
        this.target = g;
        this.currentTarget = null;
        for (var c in a) a.hasOwnProperty(c) && (b2 = a[c], this[c] = b2 ? b2(f) : f[c]);
        this.isDefaultPrevented = (null != f.defaultPrevented ? f.defaultPrevented : false === f.returnValue) ? pd : qd;
        this.isPropagationStopped = qd;
        return this;
      }
      A(b.prototype, { preventDefault: function() {
        this.defaultPrevented = true;
        var a2 = this.nativeEvent;
        a2 && (a2.preventDefault ? a2.preventDefault() : "unknown" !== typeof a2.returnValue && (a2.returnValue = false), this.isDefaultPrevented = pd);
      }, stopPropagation: function() {
        var a2 = this.nativeEvent;
        a2 && (a2.stopPropagation ? a2.stopPropagation() : "unknown" !== typeof a2.cancelBubble && (a2.cancelBubble = true), this.isPropagationStopped = pd);
      }, persist: function() {
      }, isPersistent: pd });
      return b;
    }
    var sd = { eventPhase: 0, bubbles: 0, cancelable: 0, timeStamp: function(a) {
      return a.timeStamp || Date.now();
    }, defaultPrevented: 0, isTrusted: 0 }, td = rd(sd), ud = A({}, sd, { view: 0, detail: 0 }), vd = rd(ud), wd, xd, yd, Ad = A({}, ud, { screenX: 0, screenY: 0, clientX: 0, clientY: 0, pageX: 0, pageY: 0, ctrlKey: 0, shiftKey: 0, altKey: 0, metaKey: 0, getModifierState: zd, button: 0, buttons: 0, relatedTarget: function(a) {
      return void 0 === a.relatedTarget ? a.fromElement === a.srcElement ? a.toElement : a.fromElement : a.relatedTarget;
    }, movementX: function(a) {
      if ("movementX" in a) return a.movementX;
      a !== yd && (yd && "mousemove" === a.type ? (wd = a.screenX - yd.screenX, xd = a.screenY - yd.screenY) : xd = wd = 0, yd = a);
      return wd;
    }, movementY: function(a) {
      return "movementY" in a ? a.movementY : xd;
    } }), Bd = rd(Ad), Cd = A({}, Ad, { dataTransfer: 0 }), Dd = rd(Cd), Ed = A({}, ud, { relatedTarget: 0 }), Fd = rd(Ed), Gd = A({}, sd, { animationName: 0, elapsedTime: 0, pseudoElement: 0 }), Hd = rd(Gd), Id = A({}, sd, { clipboardData: function(a) {
      return "clipboardData" in a ? a.clipboardData : window.clipboardData;
    } }), Jd = rd(Id), Kd = A({}, sd, { data: 0 }), Ld = rd(Kd), Md = {
      Esc: "Escape",
      Spacebar: " ",
      Left: "ArrowLeft",
      Up: "ArrowUp",
      Right: "ArrowRight",
      Down: "ArrowDown",
      Del: "Delete",
      Win: "OS",
      Menu: "ContextMenu",
      Apps: "ContextMenu",
      Scroll: "ScrollLock",
      MozPrintableKey: "Unidentified"
    }, Nd = {
      8: "Backspace",
      9: "Tab",
      12: "Clear",
      13: "Enter",
      16: "Shift",
      17: "Control",
      18: "Alt",
      19: "Pause",
      20: "CapsLock",
      27: "Escape",
      32: " ",
      33: "PageUp",
      34: "PageDown",
      35: "End",
      36: "Home",
      37: "ArrowLeft",
      38: "ArrowUp",
      39: "ArrowRight",
      40: "ArrowDown",
      45: "Insert",
      46: "Delete",
      112: "F1",
      113: "F2",
      114: "F3",
      115: "F4",
      116: "F5",
      117: "F6",
      118: "F7",
      119: "F8",
      120: "F9",
      121: "F10",
      122: "F11",
      123: "F12",
      144: "NumLock",
      145: "ScrollLock",
      224: "Meta"
    }, Od = { Alt: "altKey", Control: "ctrlKey", Meta: "metaKey", Shift: "shiftKey" };
    function Pd(a) {
      var b = this.nativeEvent;
      return b.getModifierState ? b.getModifierState(a) : (a = Od[a]) ? !!b[a] : false;
    }
    function zd() {
      return Pd;
    }
    var Qd = A({}, ud, { key: function(a) {
      if (a.key) {
        var b = Md[a.key] || a.key;
        if ("Unidentified" !== b) return b;
      }
      return "keypress" === a.type ? (a = od(a), 13 === a ? "Enter" : String.fromCharCode(a)) : "keydown" === a.type || "keyup" === a.type ? Nd[a.keyCode] || "Unidentified" : "";
    }, code: 0, location: 0, ctrlKey: 0, shiftKey: 0, altKey: 0, metaKey: 0, repeat: 0, locale: 0, getModifierState: zd, charCode: function(a) {
      return "keypress" === a.type ? od(a) : 0;
    }, keyCode: function(a) {
      return "keydown" === a.type || "keyup" === a.type ? a.keyCode : 0;
    }, which: function(a) {
      return "keypress" === a.type ? od(a) : "keydown" === a.type || "keyup" === a.type ? a.keyCode : 0;
    } }), Rd = rd(Qd), Sd = A({}, Ad, { pointerId: 0, width: 0, height: 0, pressure: 0, tangentialPressure: 0, tiltX: 0, tiltY: 0, twist: 0, pointerType: 0, isPrimary: 0 }), Td = rd(Sd), Ud = A({}, ud, { touches: 0, targetTouches: 0, changedTouches: 0, altKey: 0, metaKey: 0, ctrlKey: 0, shiftKey: 0, getModifierState: zd }), Vd = rd(Ud), Wd = A({}, sd, { propertyName: 0, elapsedTime: 0, pseudoElement: 0 }), Xd = rd(Wd), Yd = A({}, Ad, {
      deltaX: function(a) {
        return "deltaX" in a ? a.deltaX : "wheelDeltaX" in a ? -a.wheelDeltaX : 0;
      },
      deltaY: function(a) {
        return "deltaY" in a ? a.deltaY : "wheelDeltaY" in a ? -a.wheelDeltaY : "wheelDelta" in a ? -a.wheelDelta : 0;
      },
      deltaZ: 0,
      deltaMode: 0
    }), Zd = rd(Yd), $d = [9, 13, 27, 32], ae = ia && "CompositionEvent" in window, be = null;
    ia && "documentMode" in document && (be = document.documentMode);
    var ce = ia && "TextEvent" in window && !be, de = ia && (!ae || be && 8 < be && 11 >= be), ee = String.fromCharCode(32), fe = false;
    function ge(a, b) {
      switch (a) {
        case "keyup":
          return -1 !== $d.indexOf(b.keyCode);
        case "keydown":
          return 229 !== b.keyCode;
        case "keypress":
        case "mousedown":
        case "focusout":
          return true;
        default:
          return false;
      }
    }
    function he(a) {
      a = a.detail;
      return "object" === typeof a && "data" in a ? a.data : null;
    }
    var ie = false;
    function je(a, b) {
      switch (a) {
        case "compositionend":
          return he(b);
        case "keypress":
          if (32 !== b.which) return null;
          fe = true;
          return ee;
        case "textInput":
          return a = b.data, a === ee && fe ? null : a;
        default:
          return null;
      }
    }
    function ke(a, b) {
      if (ie) return "compositionend" === a || !ae && ge(a, b) ? (a = nd(), md = ld = kd = null, ie = false, a) : null;
      switch (a) {
        case "paste":
          return null;
        case "keypress":
          if (!(b.ctrlKey || b.altKey || b.metaKey) || b.ctrlKey && b.altKey) {
            if (b.char && 1 < b.char.length) return b.char;
            if (b.which) return String.fromCharCode(b.which);
          }
          return null;
        case "compositionend":
          return de && "ko" !== b.locale ? null : b.data;
        default:
          return null;
      }
    }
    var le = { color: true, date: true, datetime: true, "datetime-local": true, email: true, month: true, number: true, password: true, range: true, search: true, tel: true, text: true, time: true, url: true, week: true };
    function me(a) {
      var b = a && a.nodeName && a.nodeName.toLowerCase();
      return "input" === b ? !!le[a.type] : "textarea" === b ? true : false;
    }
    function ne(a, b, c, d) {
      Eb(d);
      b = oe(b, "onChange");
      0 < b.length && (c = new td("onChange", "change", null, c, d), a.push({ event: c, listeners: b }));
    }
    var pe = null, qe = null;
    function re(a) {
      se(a, 0);
    }
    function te(a) {
      var b = ue(a);
      if (Wa(b)) return a;
    }
    function ve(a, b) {
      if ("change" === a) return b;
    }
    var we = false;
    if (ia) {
      var xe;
      if (ia) {
        var ye = "oninput" in document;
        if (!ye) {
          var ze = document.createElement("div");
          ze.setAttribute("oninput", "return;");
          ye = "function" === typeof ze.oninput;
        }
        xe = ye;
      } else xe = false;
      we = xe && (!document.documentMode || 9 < document.documentMode);
    }
    function Ae() {
      pe && (pe.detachEvent("onpropertychange", Be), qe = pe = null);
    }
    function Be(a) {
      if ("value" === a.propertyName && te(qe)) {
        var b = [];
        ne(b, qe, a, xb(a));
        Jb(re, b);
      }
    }
    function Ce(a, b, c) {
      "focusin" === a ? (Ae(), pe = b, qe = c, pe.attachEvent("onpropertychange", Be)) : "focusout" === a && Ae();
    }
    function De(a) {
      if ("selectionchange" === a || "keyup" === a || "keydown" === a) return te(qe);
    }
    function Ee(a, b) {
      if ("click" === a) return te(b);
    }
    function Fe(a, b) {
      if ("input" === a || "change" === a) return te(b);
    }
    function Ge(a, b) {
      return a === b && (0 !== a || 1 / a === 1 / b) || a !== a && b !== b;
    }
    var He = "function" === typeof Object.is ? Object.is : Ge;
    function Ie(a, b) {
      if (He(a, b)) return true;
      if ("object" !== typeof a || null === a || "object" !== typeof b || null === b) return false;
      var c = Object.keys(a), d = Object.keys(b);
      if (c.length !== d.length) return false;
      for (d = 0; d < c.length; d++) {
        var e = c[d];
        if (!ja.call(b, e) || !He(a[e], b[e])) return false;
      }
      return true;
    }
    function Je(a) {
      for (; a && a.firstChild; ) a = a.firstChild;
      return a;
    }
    function Ke(a, b) {
      var c = Je(a);
      a = 0;
      for (var d; c; ) {
        if (3 === c.nodeType) {
          d = a + c.textContent.length;
          if (a <= b && d >= b) return { node: c, offset: b - a };
          a = d;
        }
        a: {
          for (; c; ) {
            if (c.nextSibling) {
              c = c.nextSibling;
              break a;
            }
            c = c.parentNode;
          }
          c = void 0;
        }
        c = Je(c);
      }
    }
    function Le(a, b) {
      return a && b ? a === b ? true : a && 3 === a.nodeType ? false : b && 3 === b.nodeType ? Le(a, b.parentNode) : "contains" in a ? a.contains(b) : a.compareDocumentPosition ? !!(a.compareDocumentPosition(b) & 16) : false : false;
    }
    function Me() {
      for (var a = window, b = Xa(); b instanceof a.HTMLIFrameElement; ) {
        try {
          var c = "string" === typeof b.contentWindow.location.href;
        } catch (d) {
          c = false;
        }
        if (c) a = b.contentWindow;
        else break;
        b = Xa(a.document);
      }
      return b;
    }
    function Ne(a) {
      var b = a && a.nodeName && a.nodeName.toLowerCase();
      return b && ("input" === b && ("text" === a.type || "search" === a.type || "tel" === a.type || "url" === a.type || "password" === a.type) || "textarea" === b || "true" === a.contentEditable);
    }
    function Oe(a) {
      var b = Me(), c = a.focusedElem, d = a.selectionRange;
      if (b !== c && c && c.ownerDocument && Le(c.ownerDocument.documentElement, c)) {
        if (null !== d && Ne(c)) {
          if (b = d.start, a = d.end, void 0 === a && (a = b), "selectionStart" in c) c.selectionStart = b, c.selectionEnd = Math.min(a, c.value.length);
          else if (a = (b = c.ownerDocument || document) && b.defaultView || window, a.getSelection) {
            a = a.getSelection();
            var e = c.textContent.length, f = Math.min(d.start, e);
            d = void 0 === d.end ? f : Math.min(d.end, e);
            !a.extend && f > d && (e = d, d = f, f = e);
            e = Ke(c, f);
            var g = Ke(
              c,
              d
            );
            e && g && (1 !== a.rangeCount || a.anchorNode !== e.node || a.anchorOffset !== e.offset || a.focusNode !== g.node || a.focusOffset !== g.offset) && (b = b.createRange(), b.setStart(e.node, e.offset), a.removeAllRanges(), f > d ? (a.addRange(b), a.extend(g.node, g.offset)) : (b.setEnd(g.node, g.offset), a.addRange(b)));
          }
        }
        b = [];
        for (a = c; a = a.parentNode; ) 1 === a.nodeType && b.push({ element: a, left: a.scrollLeft, top: a.scrollTop });
        "function" === typeof c.focus && c.focus();
        for (c = 0; c < b.length; c++) a = b[c], a.element.scrollLeft = a.left, a.element.scrollTop = a.top;
      }
    }
    var Pe = ia && "documentMode" in document && 11 >= document.documentMode, Qe = null, Re = null, Se = null, Te = false;
    function Ue(a, b, c) {
      var d = c.window === c ? c.document : 9 === c.nodeType ? c : c.ownerDocument;
      Te || null == Qe || Qe !== Xa(d) || (d = Qe, "selectionStart" in d && Ne(d) ? d = { start: d.selectionStart, end: d.selectionEnd } : (d = (d.ownerDocument && d.ownerDocument.defaultView || window).getSelection(), d = { anchorNode: d.anchorNode, anchorOffset: d.anchorOffset, focusNode: d.focusNode, focusOffset: d.focusOffset }), Se && Ie(Se, d) || (Se = d, d = oe(Re, "onSelect"), 0 < d.length && (b = new td("onSelect", "select", null, b, c), a.push({ event: b, listeners: d }), b.target = Qe)));
    }
    function Ve(a, b) {
      var c = {};
      c[a.toLowerCase()] = b.toLowerCase();
      c["Webkit" + a] = "webkit" + b;
      c["Moz" + a] = "moz" + b;
      return c;
    }
    var We = { animationend: Ve("Animation", "AnimationEnd"), animationiteration: Ve("Animation", "AnimationIteration"), animationstart: Ve("Animation", "AnimationStart"), transitionend: Ve("Transition", "TransitionEnd") }, Xe = {}, Ye = {};
    ia && (Ye = document.createElement("div").style, "AnimationEvent" in window || (delete We.animationend.animation, delete We.animationiteration.animation, delete We.animationstart.animation), "TransitionEvent" in window || delete We.transitionend.transition);
    function Ze(a) {
      if (Xe[a]) return Xe[a];
      if (!We[a]) return a;
      var b = We[a], c;
      for (c in b) if (b.hasOwnProperty(c) && c in Ye) return Xe[a] = b[c];
      return a;
    }
    var $e = Ze("animationend"), af = Ze("animationiteration"), bf = Ze("animationstart"), cf = Ze("transitionend"), df = /* @__PURE__ */ new Map(), ef = "abort auxClick cancel canPlay canPlayThrough click close contextMenu copy cut drag dragEnd dragEnter dragExit dragLeave dragOver dragStart drop durationChange emptied encrypted ended error gotPointerCapture input invalid keyDown keyPress keyUp load loadedData loadedMetadata loadStart lostPointerCapture mouseDown mouseMove mouseOut mouseOver mouseUp paste pause play playing pointerCancel pointerDown pointerMove pointerOut pointerOver pointerUp progress rateChange reset resize seeked seeking stalled submit suspend timeUpdate touchCancel touchEnd touchStart volumeChange scroll toggle touchMove waiting wheel".split(" ");
    function ff(a, b) {
      df.set(a, b);
      fa(b, [a]);
    }
    for (var gf = 0; gf < ef.length; gf++) {
      var hf = ef[gf], jf = hf.toLowerCase(), kf = hf[0].toUpperCase() + hf.slice(1);
      ff(jf, "on" + kf);
    }
    ff($e, "onAnimationEnd");
    ff(af, "onAnimationIteration");
    ff(bf, "onAnimationStart");
    ff("dblclick", "onDoubleClick");
    ff("focusin", "onFocus");
    ff("focusout", "onBlur");
    ff(cf, "onTransitionEnd");
    ha("onMouseEnter", ["mouseout", "mouseover"]);
    ha("onMouseLeave", ["mouseout", "mouseover"]);
    ha("onPointerEnter", ["pointerout", "pointerover"]);
    ha("onPointerLeave", ["pointerout", "pointerover"]);
    fa("onChange", "change click focusin focusout input keydown keyup selectionchange".split(" "));
    fa("onSelect", "focusout contextmenu dragend focusin keydown keyup mousedown mouseup selectionchange".split(" "));
    fa("onBeforeInput", ["compositionend", "keypress", "textInput", "paste"]);
    fa("onCompositionEnd", "compositionend focusout keydown keypress keyup mousedown".split(" "));
    fa("onCompositionStart", "compositionstart focusout keydown keypress keyup mousedown".split(" "));
    fa("onCompositionUpdate", "compositionupdate focusout keydown keypress keyup mousedown".split(" "));
    var lf = "abort canplay canplaythrough durationchange emptied encrypted ended error loadeddata loadedmetadata loadstart pause play playing progress ratechange resize seeked seeking stalled suspend timeupdate volumechange waiting".split(" "), mf = new Set("cancel close invalid load scroll toggle".split(" ").concat(lf));
    function nf(a, b, c) {
      var d = a.type || "unknown-event";
      a.currentTarget = c;
      Ub(d, b, void 0, a);
      a.currentTarget = null;
    }
    function se(a, b) {
      b = 0 !== (b & 4);
      for (var c = 0; c < a.length; c++) {
        var d = a[c], e = d.event;
        d = d.listeners;
        a: {
          var f = void 0;
          if (b) for (var g = d.length - 1; 0 <= g; g--) {
            var h = d[g], k = h.instance, l = h.currentTarget;
            h = h.listener;
            if (k !== f && e.isPropagationStopped()) break a;
            nf(e, h, l);
            f = k;
          }
          else for (g = 0; g < d.length; g++) {
            h = d[g];
            k = h.instance;
            l = h.currentTarget;
            h = h.listener;
            if (k !== f && e.isPropagationStopped()) break a;
            nf(e, h, l);
            f = k;
          }
        }
      }
      if (Qb) throw a = Rb, Qb = false, Rb = null, a;
    }
    function D(a, b) {
      var c = b[of];
      void 0 === c && (c = b[of] = /* @__PURE__ */ new Set());
      var d = a + "__bubble";
      c.has(d) || (pf(b, a, 2, false), c.add(d));
    }
    function qf(a, b, c) {
      var d = 0;
      b && (d |= 4);
      pf(c, a, d, b);
    }
    var rf = "_reactListening" + Math.random().toString(36).slice(2);
    function sf(a) {
      if (!a[rf]) {
        a[rf] = true;
        da.forEach(function(b2) {
          "selectionchange" !== b2 && (mf.has(b2) || qf(b2, false, a), qf(b2, true, a));
        });
        var b = 9 === a.nodeType ? a : a.ownerDocument;
        null === b || b[rf] || (b[rf] = true, qf("selectionchange", false, b));
      }
    }
    function pf(a, b, c, d) {
      switch (jd(b)) {
        case 1:
          var e = ed;
          break;
        case 4:
          e = gd;
          break;
        default:
          e = fd;
      }
      c = e.bind(null, b, c, a);
      e = void 0;
      !Lb || "touchstart" !== b && "touchmove" !== b && "wheel" !== b || (e = true);
      d ? void 0 !== e ? a.addEventListener(b, c, { capture: true, passive: e }) : a.addEventListener(b, c, true) : void 0 !== e ? a.addEventListener(b, c, { passive: e }) : a.addEventListener(b, c, false);
    }
    function hd(a, b, c, d, e) {
      var f = d;
      if (0 === (b & 1) && 0 === (b & 2) && null !== d) a: for (; ; ) {
        if (null === d) return;
        var g = d.tag;
        if (3 === g || 4 === g) {
          var h = d.stateNode.containerInfo;
          if (h === e || 8 === h.nodeType && h.parentNode === e) break;
          if (4 === g) for (g = d.return; null !== g; ) {
            var k = g.tag;
            if (3 === k || 4 === k) {
              if (k = g.stateNode.containerInfo, k === e || 8 === k.nodeType && k.parentNode === e) return;
            }
            g = g.return;
          }
          for (; null !== h; ) {
            g = Wc(h);
            if (null === g) return;
            k = g.tag;
            if (5 === k || 6 === k) {
              d = f = g;
              continue a;
            }
            h = h.parentNode;
          }
        }
        d = d.return;
      }
      Jb(function() {
        var d2 = f, e2 = xb(c), g2 = [];
        a: {
          var h2 = df.get(a);
          if (void 0 !== h2) {
            var k2 = td, n = a;
            switch (a) {
              case "keypress":
                if (0 === od(c)) break a;
              case "keydown":
              case "keyup":
                k2 = Rd;
                break;
              case "focusin":
                n = "focus";
                k2 = Fd;
                break;
              case "focusout":
                n = "blur";
                k2 = Fd;
                break;
              case "beforeblur":
              case "afterblur":
                k2 = Fd;
                break;
              case "click":
                if (2 === c.button) break a;
              case "auxclick":
              case "dblclick":
              case "mousedown":
              case "mousemove":
              case "mouseup":
              case "mouseout":
              case "mouseover":
              case "contextmenu":
                k2 = Bd;
                break;
              case "drag":
              case "dragend":
              case "dragenter":
              case "dragexit":
              case "dragleave":
              case "dragover":
              case "dragstart":
              case "drop":
                k2 = Dd;
                break;
              case "touchcancel":
              case "touchend":
              case "touchmove":
              case "touchstart":
                k2 = Vd;
                break;
              case $e:
              case af:
              case bf:
                k2 = Hd;
                break;
              case cf:
                k2 = Xd;
                break;
              case "scroll":
                k2 = vd;
                break;
              case "wheel":
                k2 = Zd;
                break;
              case "copy":
              case "cut":
              case "paste":
                k2 = Jd;
                break;
              case "gotpointercapture":
              case "lostpointercapture":
              case "pointercancel":
              case "pointerdown":
              case "pointermove":
              case "pointerout":
              case "pointerover":
              case "pointerup":
                k2 = Td;
            }
            var t = 0 !== (b & 4), J = !t && "scroll" === a, x = t ? null !== h2 ? h2 + "Capture" : null : h2;
            t = [];
            for (var w = d2, u; null !== w; ) {
              u = w;
              var F = u.stateNode;
              5 === u.tag && null !== F && (u = F, null !== x && (F = Kb(w, x), null != F && t.push(tf(w, F, u))));
              if (J) break;
              w = w.return;
            }
            0 < t.length && (h2 = new k2(h2, n, null, c, e2), g2.push({ event: h2, listeners: t }));
          }
        }
        if (0 === (b & 7)) {
          a: {
            h2 = "mouseover" === a || "pointerover" === a;
            k2 = "mouseout" === a || "pointerout" === a;
            if (h2 && c !== wb && (n = c.relatedTarget || c.fromElement) && (Wc(n) || n[uf])) break a;
            if (k2 || h2) {
              h2 = e2.window === e2 ? e2 : (h2 = e2.ownerDocument) ? h2.defaultView || h2.parentWindow : window;
              if (k2) {
                if (n = c.relatedTarget || c.toElement, k2 = d2, n = n ? Wc(n) : null, null !== n && (J = Vb(n), n !== J || 5 !== n.tag && 6 !== n.tag)) n = null;
              } else k2 = null, n = d2;
              if (k2 !== n) {
                t = Bd;
                F = "onMouseLeave";
                x = "onMouseEnter";
                w = "mouse";
                if ("pointerout" === a || "pointerover" === a) t = Td, F = "onPointerLeave", x = "onPointerEnter", w = "pointer";
                J = null == k2 ? h2 : ue(k2);
                u = null == n ? h2 : ue(n);
                h2 = new t(F, w + "leave", k2, c, e2);
                h2.target = J;
                h2.relatedTarget = u;
                F = null;
                Wc(e2) === d2 && (t = new t(x, w + "enter", n, c, e2), t.target = u, t.relatedTarget = J, F = t);
                J = F;
                if (k2 && n) b: {
                  t = k2;
                  x = n;
                  w = 0;
                  for (u = t; u; u = vf(u)) w++;
                  u = 0;
                  for (F = x; F; F = vf(F)) u++;
                  for (; 0 < w - u; ) t = vf(t), w--;
                  for (; 0 < u - w; ) x = vf(x), u--;
                  for (; w--; ) {
                    if (t === x || null !== x && t === x.alternate) break b;
                    t = vf(t);
                    x = vf(x);
                  }
                  t = null;
                }
                else t = null;
                null !== k2 && wf(g2, h2, k2, t, false);
                null !== n && null !== J && wf(g2, J, n, t, true);
              }
            }
          }
          a: {
            h2 = d2 ? ue(d2) : window;
            k2 = h2.nodeName && h2.nodeName.toLowerCase();
            if ("select" === k2 || "input" === k2 && "file" === h2.type) var na = ve;
            else if (me(h2)) if (we) na = Fe;
            else {
              na = De;
              var xa = Ce;
            }
            else (k2 = h2.nodeName) && "input" === k2.toLowerCase() && ("checkbox" === h2.type || "radio" === h2.type) && (na = Ee);
            if (na && (na = na(a, d2))) {
              ne(g2, na, c, e2);
              break a;
            }
            xa && xa(a, h2, d2);
            "focusout" === a && (xa = h2._wrapperState) && xa.controlled && "number" === h2.type && cb(h2, "number", h2.value);
          }
          xa = d2 ? ue(d2) : window;
          switch (a) {
            case "focusin":
              if (me(xa) || "true" === xa.contentEditable) Qe = xa, Re = d2, Se = null;
              break;
            case "focusout":
              Se = Re = Qe = null;
              break;
            case "mousedown":
              Te = true;
              break;
            case "contextmenu":
            case "mouseup":
            case "dragend":
              Te = false;
              Ue(g2, c, e2);
              break;
            case "selectionchange":
              if (Pe) break;
            case "keydown":
            case "keyup":
              Ue(g2, c, e2);
          }
          var $a;
          if (ae) b: {
            switch (a) {
              case "compositionstart":
                var ba = "onCompositionStart";
                break b;
              case "compositionend":
                ba = "onCompositionEnd";
                break b;
              case "compositionupdate":
                ba = "onCompositionUpdate";
                break b;
            }
            ba = void 0;
          }
          else ie ? ge(a, c) && (ba = "onCompositionEnd") : "keydown" === a && 229 === c.keyCode && (ba = "onCompositionStart");
          ba && (de && "ko" !== c.locale && (ie || "onCompositionStart" !== ba ? "onCompositionEnd" === ba && ie && ($a = nd()) : (kd = e2, ld = "value" in kd ? kd.value : kd.textContent, ie = true)), xa = oe(d2, ba), 0 < xa.length && (ba = new Ld(ba, a, null, c, e2), g2.push({ event: ba, listeners: xa }), $a ? ba.data = $a : ($a = he(c), null !== $a && (ba.data = $a))));
          if ($a = ce ? je(a, c) : ke(a, c)) d2 = oe(d2, "onBeforeInput"), 0 < d2.length && (e2 = new Ld("onBeforeInput", "beforeinput", null, c, e2), g2.push({ event: e2, listeners: d2 }), e2.data = $a);
        }
        se(g2, b);
      });
    }
    function tf(a, b, c) {
      return { instance: a, listener: b, currentTarget: c };
    }
    function oe(a, b) {
      for (var c = b + "Capture", d = []; null !== a; ) {
        var e = a, f = e.stateNode;
        5 === e.tag && null !== f && (e = f, f = Kb(a, c), null != f && d.unshift(tf(a, f, e)), f = Kb(a, b), null != f && d.push(tf(a, f, e)));
        a = a.return;
      }
      return d;
    }
    function vf(a) {
      if (null === a) return null;
      do
        a = a.return;
      while (a && 5 !== a.tag);
      return a ? a : null;
    }
    function wf(a, b, c, d, e) {
      for (var f = b._reactName, g = []; null !== c && c !== d; ) {
        var h = c, k = h.alternate, l = h.stateNode;
        if (null !== k && k === d) break;
        5 === h.tag && null !== l && (h = l, e ? (k = Kb(c, f), null != k && g.unshift(tf(c, k, h))) : e || (k = Kb(c, f), null != k && g.push(tf(c, k, h))));
        c = c.return;
      }
      0 !== g.length && a.push({ event: b, listeners: g });
    }
    var xf = /\r\n?/g, yf = /\u0000|\uFFFD/g;
    function zf(a) {
      return ("string" === typeof a ? a : "" + a).replace(xf, "\n").replace(yf, "");
    }
    function Af(a, b, c) {
      b = zf(b);
      if (zf(a) !== b && c) throw Error(p(425));
    }
    function Bf() {
    }
    var Cf = null, Df = null;
    function Ef(a, b) {
      return "textarea" === a || "noscript" === a || "string" === typeof b.children || "number" === typeof b.children || "object" === typeof b.dangerouslySetInnerHTML && null !== b.dangerouslySetInnerHTML && null != b.dangerouslySetInnerHTML.__html;
    }
    var Ff = "function" === typeof setTimeout ? setTimeout : void 0, Gf = "function" === typeof clearTimeout ? clearTimeout : void 0, Hf = "function" === typeof Promise ? Promise : void 0, Jf = "function" === typeof queueMicrotask ? queueMicrotask : "undefined" !== typeof Hf ? function(a) {
      return Hf.resolve(null).then(a).catch(If);
    } : Ff;
    function If(a) {
      setTimeout(function() {
        throw a;
      });
    }
    function Kf(a, b) {
      var c = b, d = 0;
      do {
        var e = c.nextSibling;
        a.removeChild(c);
        if (e && 8 === e.nodeType) if (c = e.data, "/$" === c) {
          if (0 === d) {
            a.removeChild(e);
            bd(b);
            return;
          }
          d--;
        } else "$" !== c && "$?" !== c && "$!" !== c || d++;
        c = e;
      } while (c);
      bd(b);
    }
    function Lf(a) {
      for (; null != a; a = a.nextSibling) {
        var b = a.nodeType;
        if (1 === b || 3 === b) break;
        if (8 === b) {
          b = a.data;
          if ("$" === b || "$!" === b || "$?" === b) break;
          if ("/$" === b) return null;
        }
      }
      return a;
    }
    function Mf(a) {
      a = a.previousSibling;
      for (var b = 0; a; ) {
        if (8 === a.nodeType) {
          var c = a.data;
          if ("$" === c || "$!" === c || "$?" === c) {
            if (0 === b) return a;
            b--;
          } else "/$" === c && b++;
        }
        a = a.previousSibling;
      }
      return null;
    }
    var Nf = Math.random().toString(36).slice(2), Of = "__reactFiber$" + Nf, Pf = "__reactProps$" + Nf, uf = "__reactContainer$" + Nf, of = "__reactEvents$" + Nf, Qf = "__reactListeners$" + Nf, Rf = "__reactHandles$" + Nf;
    function Wc(a) {
      var b = a[Of];
      if (b) return b;
      for (var c = a.parentNode; c; ) {
        if (b = c[uf] || c[Of]) {
          c = b.alternate;
          if (null !== b.child || null !== c && null !== c.child) for (a = Mf(a); null !== a; ) {
            if (c = a[Of]) return c;
            a = Mf(a);
          }
          return b;
        }
        a = c;
        c = a.parentNode;
      }
      return null;
    }
    function Cb(a) {
      a = a[Of] || a[uf];
      return !a || 5 !== a.tag && 6 !== a.tag && 13 !== a.tag && 3 !== a.tag ? null : a;
    }
    function ue(a) {
      if (5 === a.tag || 6 === a.tag) return a.stateNode;
      throw Error(p(33));
    }
    function Db(a) {
      return a[Pf] || null;
    }
    var Sf = [], Tf = -1;
    function Uf(a) {
      return { current: a };
    }
    function E(a) {
      0 > Tf || (a.current = Sf[Tf], Sf[Tf] = null, Tf--);
    }
    function G(a, b) {
      Tf++;
      Sf[Tf] = a.current;
      a.current = b;
    }
    var Vf = {}, H = Uf(Vf), Wf = Uf(false), Xf = Vf;
    function Yf(a, b) {
      var c = a.type.contextTypes;
      if (!c) return Vf;
      var d = a.stateNode;
      if (d && d.__reactInternalMemoizedUnmaskedChildContext === b) return d.__reactInternalMemoizedMaskedChildContext;
      var e = {}, f;
      for (f in c) e[f] = b[f];
      d && (a = a.stateNode, a.__reactInternalMemoizedUnmaskedChildContext = b, a.__reactInternalMemoizedMaskedChildContext = e);
      return e;
    }
    function Zf(a) {
      a = a.childContextTypes;
      return null !== a && void 0 !== a;
    }
    function $f() {
      E(Wf);
      E(H);
    }
    function ag(a, b, c) {
      if (H.current !== Vf) throw Error(p(168));
      G(H, b);
      G(Wf, c);
    }
    function bg(a, b, c) {
      var d = a.stateNode;
      b = b.childContextTypes;
      if ("function" !== typeof d.getChildContext) return c;
      d = d.getChildContext();
      for (var e in d) if (!(e in b)) throw Error(p(108, Ra(a) || "Unknown", e));
      return A({}, c, d);
    }
    function cg(a) {
      a = (a = a.stateNode) && a.__reactInternalMemoizedMergedChildContext || Vf;
      Xf = H.current;
      G(H, a);
      G(Wf, Wf.current);
      return true;
    }
    function dg(a, b, c) {
      var d = a.stateNode;
      if (!d) throw Error(p(169));
      c ? (a = bg(a, b, Xf), d.__reactInternalMemoizedMergedChildContext = a, E(Wf), E(H), G(H, a)) : E(Wf);
      G(Wf, c);
    }
    var eg = null, fg = false, gg = false;
    function hg(a) {
      null === eg ? eg = [a] : eg.push(a);
    }
    function ig(a) {
      fg = true;
      hg(a);
    }
    function jg() {
      if (!gg && null !== eg) {
        gg = true;
        var a = 0, b = C;
        try {
          var c = eg;
          for (C = 1; a < c.length; a++) {
            var d = c[a];
            do
              d = d(true);
            while (null !== d);
          }
          eg = null;
          fg = false;
        } catch (e) {
          throw null !== eg && (eg = eg.slice(a + 1)), ac(fc, jg), e;
        } finally {
          C = b, gg = false;
        }
      }
      return null;
    }
    var kg = [], lg = 0, mg = null, ng = 0, og = [], pg = 0, qg = null, rg = 1, sg = "";
    function tg(a, b) {
      kg[lg++] = ng;
      kg[lg++] = mg;
      mg = a;
      ng = b;
    }
    function ug(a, b, c) {
      og[pg++] = rg;
      og[pg++] = sg;
      og[pg++] = qg;
      qg = a;
      var d = rg;
      a = sg;
      var e = 32 - oc(d) - 1;
      d &= ~(1 << e);
      c += 1;
      var f = 32 - oc(b) + e;
      if (30 < f) {
        var g = e - e % 5;
        f = (d & (1 << g) - 1).toString(32);
        d >>= g;
        e -= g;
        rg = 1 << 32 - oc(b) + e | c << e | d;
        sg = f + a;
      } else rg = 1 << f | c << e | d, sg = a;
    }
    function vg(a) {
      null !== a.return && (tg(a, 1), ug(a, 1, 0));
    }
    function wg(a) {
      for (; a === mg; ) mg = kg[--lg], kg[lg] = null, ng = kg[--lg], kg[lg] = null;
      for (; a === qg; ) qg = og[--pg], og[pg] = null, sg = og[--pg], og[pg] = null, rg = og[--pg], og[pg] = null;
    }
    var xg = null, yg = null, I = false, zg = null;
    function Ag(a, b) {
      var c = Bg(5, null, null, 0);
      c.elementType = "DELETED";
      c.stateNode = b;
      c.return = a;
      b = a.deletions;
      null === b ? (a.deletions = [c], a.flags |= 16) : b.push(c);
    }
    function Cg(a, b) {
      switch (a.tag) {
        case 5:
          var c = a.type;
          b = 1 !== b.nodeType || c.toLowerCase() !== b.nodeName.toLowerCase() ? null : b;
          return null !== b ? (a.stateNode = b, xg = a, yg = Lf(b.firstChild), true) : false;
        case 6:
          return b = "" === a.pendingProps || 3 !== b.nodeType ? null : b, null !== b ? (a.stateNode = b, xg = a, yg = null, true) : false;
        case 13:
          return b = 8 !== b.nodeType ? null : b, null !== b ? (c = null !== qg ? { id: rg, overflow: sg } : null, a.memoizedState = { dehydrated: b, treeContext: c, retryLane: 1073741824 }, c = Bg(18, null, null, 0), c.stateNode = b, c.return = a, a.child = c, xg = a, yg = null, true) : false;
        default:
          return false;
      }
    }
    function Dg(a) {
      return 0 !== (a.mode & 1) && 0 === (a.flags & 128);
    }
    function Eg(a) {
      if (I) {
        var b = yg;
        if (b) {
          var c = b;
          if (!Cg(a, b)) {
            if (Dg(a)) throw Error(p(418));
            b = Lf(c.nextSibling);
            var d = xg;
            b && Cg(a, b) ? Ag(d, c) : (a.flags = a.flags & -4097 | 2, I = false, xg = a);
          }
        } else {
          if (Dg(a)) throw Error(p(418));
          a.flags = a.flags & -4097 | 2;
          I = false;
          xg = a;
        }
      }
    }
    function Fg(a) {
      for (a = a.return; null !== a && 5 !== a.tag && 3 !== a.tag && 13 !== a.tag; ) a = a.return;
      xg = a;
    }
    function Gg(a) {
      if (a !== xg) return false;
      if (!I) return Fg(a), I = true, false;
      var b;
      (b = 3 !== a.tag) && !(b = 5 !== a.tag) && (b = a.type, b = "head" !== b && "body" !== b && !Ef(a.type, a.memoizedProps));
      if (b && (b = yg)) {
        if (Dg(a)) throw Hg(), Error(p(418));
        for (; b; ) Ag(a, b), b = Lf(b.nextSibling);
      }
      Fg(a);
      if (13 === a.tag) {
        a = a.memoizedState;
        a = null !== a ? a.dehydrated : null;
        if (!a) throw Error(p(317));
        a: {
          a = a.nextSibling;
          for (b = 0; a; ) {
            if (8 === a.nodeType) {
              var c = a.data;
              if ("/$" === c) {
                if (0 === b) {
                  yg = Lf(a.nextSibling);
                  break a;
                }
                b--;
              } else "$" !== c && "$!" !== c && "$?" !== c || b++;
            }
            a = a.nextSibling;
          }
          yg = null;
        }
      } else yg = xg ? Lf(a.stateNode.nextSibling) : null;
      return true;
    }
    function Hg() {
      for (var a = yg; a; ) a = Lf(a.nextSibling);
    }
    function Ig() {
      yg = xg = null;
      I = false;
    }
    function Jg(a) {
      null === zg ? zg = [a] : zg.push(a);
    }
    var Kg = ua.ReactCurrentBatchConfig;
    function Lg(a, b, c) {
      a = c.ref;
      if (null !== a && "function" !== typeof a && "object" !== typeof a) {
        if (c._owner) {
          c = c._owner;
          if (c) {
            if (1 !== c.tag) throw Error(p(309));
            var d = c.stateNode;
          }
          if (!d) throw Error(p(147, a));
          var e = d, f = "" + a;
          if (null !== b && null !== b.ref && "function" === typeof b.ref && b.ref._stringRef === f) return b.ref;
          b = function(a2) {
            var b2 = e.refs;
            null === a2 ? delete b2[f] : b2[f] = a2;
          };
          b._stringRef = f;
          return b;
        }
        if ("string" !== typeof a) throw Error(p(284));
        if (!c._owner) throw Error(p(290, a));
      }
      return a;
    }
    function Mg(a, b) {
      a = Object.prototype.toString.call(b);
      throw Error(p(31, "[object Object]" === a ? "object with keys {" + Object.keys(b).join(", ") + "}" : a));
    }
    function Ng(a) {
      var b = a._init;
      return b(a._payload);
    }
    function Og(a) {
      function b(b2, c2) {
        if (a) {
          var d2 = b2.deletions;
          null === d2 ? (b2.deletions = [c2], b2.flags |= 16) : d2.push(c2);
        }
      }
      function c(c2, d2) {
        if (!a) return null;
        for (; null !== d2; ) b(c2, d2), d2 = d2.sibling;
        return null;
      }
      function d(a2, b2) {
        for (a2 = /* @__PURE__ */ new Map(); null !== b2; ) null !== b2.key ? a2.set(b2.key, b2) : a2.set(b2.index, b2), b2 = b2.sibling;
        return a2;
      }
      function e(a2, b2) {
        a2 = Pg(a2, b2);
        a2.index = 0;
        a2.sibling = null;
        return a2;
      }
      function f(b2, c2, d2) {
        b2.index = d2;
        if (!a) return b2.flags |= 1048576, c2;
        d2 = b2.alternate;
        if (null !== d2) return d2 = d2.index, d2 < c2 ? (b2.flags |= 2, c2) : d2;
        b2.flags |= 2;
        return c2;
      }
      function g(b2) {
        a && null === b2.alternate && (b2.flags |= 2);
        return b2;
      }
      function h(a2, b2, c2, d2) {
        if (null === b2 || 6 !== b2.tag) return b2 = Qg(c2, a2.mode, d2), b2.return = a2, b2;
        b2 = e(b2, c2);
        b2.return = a2;
        return b2;
      }
      function k(a2, b2, c2, d2) {
        var f2 = c2.type;
        if (f2 === ya) return m(a2, b2, c2.props.children, d2, c2.key);
        if (null !== b2 && (b2.elementType === f2 || "object" === typeof f2 && null !== f2 && f2.$$typeof === Ha && Ng(f2) === b2.type)) return d2 = e(b2, c2.props), d2.ref = Lg(a2, b2, c2), d2.return = a2, d2;
        d2 = Rg(c2.type, c2.key, c2.props, null, a2.mode, d2);
        d2.ref = Lg(a2, b2, c2);
        d2.return = a2;
        return d2;
      }
      function l(a2, b2, c2, d2) {
        if (null === b2 || 4 !== b2.tag || b2.stateNode.containerInfo !== c2.containerInfo || b2.stateNode.implementation !== c2.implementation) return b2 = Sg(c2, a2.mode, d2), b2.return = a2, b2;
        b2 = e(b2, c2.children || []);
        b2.return = a2;
        return b2;
      }
      function m(a2, b2, c2, d2, f2) {
        if (null === b2 || 7 !== b2.tag) return b2 = Tg(c2, a2.mode, d2, f2), b2.return = a2, b2;
        b2 = e(b2, c2);
        b2.return = a2;
        return b2;
      }
      function q(a2, b2, c2) {
        if ("string" === typeof b2 && "" !== b2 || "number" === typeof b2) return b2 = Qg("" + b2, a2.mode, c2), b2.return = a2, b2;
        if ("object" === typeof b2 && null !== b2) {
          switch (b2.$$typeof) {
            case va:
              return c2 = Rg(b2.type, b2.key, b2.props, null, a2.mode, c2), c2.ref = Lg(a2, null, b2), c2.return = a2, c2;
            case wa:
              return b2 = Sg(b2, a2.mode, c2), b2.return = a2, b2;
            case Ha:
              var d2 = b2._init;
              return q(a2, d2(b2._payload), c2);
          }
          if (eb(b2) || Ka(b2)) return b2 = Tg(b2, a2.mode, c2, null), b2.return = a2, b2;
          Mg(a2, b2);
        }
        return null;
      }
      function r(a2, b2, c2, d2) {
        var e2 = null !== b2 ? b2.key : null;
        if ("string" === typeof c2 && "" !== c2 || "number" === typeof c2) return null !== e2 ? null : h(a2, b2, "" + c2, d2);
        if ("object" === typeof c2 && null !== c2) {
          switch (c2.$$typeof) {
            case va:
              return c2.key === e2 ? k(a2, b2, c2, d2) : null;
            case wa:
              return c2.key === e2 ? l(a2, b2, c2, d2) : null;
            case Ha:
              return e2 = c2._init, r(
                a2,
                b2,
                e2(c2._payload),
                d2
              );
          }
          if (eb(c2) || Ka(c2)) return null !== e2 ? null : m(a2, b2, c2, d2, null);
          Mg(a2, c2);
        }
        return null;
      }
      function y(a2, b2, c2, d2, e2) {
        if ("string" === typeof d2 && "" !== d2 || "number" === typeof d2) return a2 = a2.get(c2) || null, h(b2, a2, "" + d2, e2);
        if ("object" === typeof d2 && null !== d2) {
          switch (d2.$$typeof) {
            case va:
              return a2 = a2.get(null === d2.key ? c2 : d2.key) || null, k(b2, a2, d2, e2);
            case wa:
              return a2 = a2.get(null === d2.key ? c2 : d2.key) || null, l(b2, a2, d2, e2);
            case Ha:
              var f2 = d2._init;
              return y(a2, b2, c2, f2(d2._payload), e2);
          }
          if (eb(d2) || Ka(d2)) return a2 = a2.get(c2) || null, m(b2, a2, d2, e2, null);
          Mg(b2, d2);
        }
        return null;
      }
      function n(e2, g2, h2, k2) {
        for (var l2 = null, m2 = null, u = g2, w = g2 = 0, x = null; null !== u && w < h2.length; w++) {
          u.index > w ? (x = u, u = null) : x = u.sibling;
          var n2 = r(e2, u, h2[w], k2);
          if (null === n2) {
            null === u && (u = x);
            break;
          }
          a && u && null === n2.alternate && b(e2, u);
          g2 = f(n2, g2, w);
          null === m2 ? l2 = n2 : m2.sibling = n2;
          m2 = n2;
          u = x;
        }
        if (w === h2.length) return c(e2, u), I && tg(e2, w), l2;
        if (null === u) {
          for (; w < h2.length; w++) u = q(e2, h2[w], k2), null !== u && (g2 = f(u, g2, w), null === m2 ? l2 = u : m2.sibling = u, m2 = u);
          I && tg(e2, w);
          return l2;
        }
        for (u = d(e2, u); w < h2.length; w++) x = y(u, e2, w, h2[w], k2), null !== x && (a && null !== x.alternate && u.delete(null === x.key ? w : x.key), g2 = f(x, g2, w), null === m2 ? l2 = x : m2.sibling = x, m2 = x);
        a && u.forEach(function(a2) {
          return b(e2, a2);
        });
        I && tg(e2, w);
        return l2;
      }
      function t(e2, g2, h2, k2) {
        var l2 = Ka(h2);
        if ("function" !== typeof l2) throw Error(p(150));
        h2 = l2.call(h2);
        if (null == h2) throw Error(p(151));
        for (var u = l2 = null, m2 = g2, w = g2 = 0, x = null, n2 = h2.next(); null !== m2 && !n2.done; w++, n2 = h2.next()) {
          m2.index > w ? (x = m2, m2 = null) : x = m2.sibling;
          var t2 = r(e2, m2, n2.value, k2);
          if (null === t2) {
            null === m2 && (m2 = x);
            break;
          }
          a && m2 && null === t2.alternate && b(e2, m2);
          g2 = f(t2, g2, w);
          null === u ? l2 = t2 : u.sibling = t2;
          u = t2;
          m2 = x;
        }
        if (n2.done) return c(
          e2,
          m2
        ), I && tg(e2, w), l2;
        if (null === m2) {
          for (; !n2.done; w++, n2 = h2.next()) n2 = q(e2, n2.value, k2), null !== n2 && (g2 = f(n2, g2, w), null === u ? l2 = n2 : u.sibling = n2, u = n2);
          I && tg(e2, w);
          return l2;
        }
        for (m2 = d(e2, m2); !n2.done; w++, n2 = h2.next()) n2 = y(m2, e2, w, n2.value, k2), null !== n2 && (a && null !== n2.alternate && m2.delete(null === n2.key ? w : n2.key), g2 = f(n2, g2, w), null === u ? l2 = n2 : u.sibling = n2, u = n2);
        a && m2.forEach(function(a2) {
          return b(e2, a2);
        });
        I && tg(e2, w);
        return l2;
      }
      function J(a2, d2, f2, h2) {
        "object" === typeof f2 && null !== f2 && f2.type === ya && null === f2.key && (f2 = f2.props.children);
        if ("object" === typeof f2 && null !== f2) {
          switch (f2.$$typeof) {
            case va:
              a: {
                for (var k2 = f2.key, l2 = d2; null !== l2; ) {
                  if (l2.key === k2) {
                    k2 = f2.type;
                    if (k2 === ya) {
                      if (7 === l2.tag) {
                        c(a2, l2.sibling);
                        d2 = e(l2, f2.props.children);
                        d2.return = a2;
                        a2 = d2;
                        break a;
                      }
                    } else if (l2.elementType === k2 || "object" === typeof k2 && null !== k2 && k2.$$typeof === Ha && Ng(k2) === l2.type) {
                      c(a2, l2.sibling);
                      d2 = e(l2, f2.props);
                      d2.ref = Lg(a2, l2, f2);
                      d2.return = a2;
                      a2 = d2;
                      break a;
                    }
                    c(a2, l2);
                    break;
                  } else b(a2, l2);
                  l2 = l2.sibling;
                }
                f2.type === ya ? (d2 = Tg(f2.props.children, a2.mode, h2, f2.key), d2.return = a2, a2 = d2) : (h2 = Rg(f2.type, f2.key, f2.props, null, a2.mode, h2), h2.ref = Lg(a2, d2, f2), h2.return = a2, a2 = h2);
              }
              return g(a2);
            case wa:
              a: {
                for (l2 = f2.key; null !== d2; ) {
                  if (d2.key === l2) if (4 === d2.tag && d2.stateNode.containerInfo === f2.containerInfo && d2.stateNode.implementation === f2.implementation) {
                    c(a2, d2.sibling);
                    d2 = e(d2, f2.children || []);
                    d2.return = a2;
                    a2 = d2;
                    break a;
                  } else {
                    c(a2, d2);
                    break;
                  }
                  else b(a2, d2);
                  d2 = d2.sibling;
                }
                d2 = Sg(f2, a2.mode, h2);
                d2.return = a2;
                a2 = d2;
              }
              return g(a2);
            case Ha:
              return l2 = f2._init, J(a2, d2, l2(f2._payload), h2);
          }
          if (eb(f2)) return n(a2, d2, f2, h2);
          if (Ka(f2)) return t(a2, d2, f2, h2);
          Mg(a2, f2);
        }
        return "string" === typeof f2 && "" !== f2 || "number" === typeof f2 ? (f2 = "" + f2, null !== d2 && 6 === d2.tag ? (c(a2, d2.sibling), d2 = e(d2, f2), d2.return = a2, a2 = d2) : (c(a2, d2), d2 = Qg(f2, a2.mode, h2), d2.return = a2, a2 = d2), g(a2)) : c(a2, d2);
      }
      return J;
    }
    var Ug = Og(true), Vg = Og(false), Wg = Uf(null), Xg = null, Yg = null, Zg = null;
    function $g() {
      Zg = Yg = Xg = null;
    }
    function ah(a) {
      var b = Wg.current;
      E(Wg);
      a._currentValue = b;
    }
    function bh(a, b, c) {
      for (; null !== a; ) {
        var d = a.alternate;
        (a.childLanes & b) !== b ? (a.childLanes |= b, null !== d && (d.childLanes |= b)) : null !== d && (d.childLanes & b) !== b && (d.childLanes |= b);
        if (a === c) break;
        a = a.return;
      }
    }
    function ch(a, b) {
      Xg = a;
      Zg = Yg = null;
      a = a.dependencies;
      null !== a && null !== a.firstContext && (0 !== (a.lanes & b) && (dh = true), a.firstContext = null);
    }
    function eh(a) {
      var b = a._currentValue;
      if (Zg !== a) if (a = { context: a, memoizedValue: b, next: null }, null === Yg) {
        if (null === Xg) throw Error(p(308));
        Yg = a;
        Xg.dependencies = { lanes: 0, firstContext: a };
      } else Yg = Yg.next = a;
      return b;
    }
    var fh = null;
    function gh(a) {
      null === fh ? fh = [a] : fh.push(a);
    }
    function hh(a, b, c, d) {
      var e = b.interleaved;
      null === e ? (c.next = c, gh(b)) : (c.next = e.next, e.next = c);
      b.interleaved = c;
      return ih(a, d);
    }
    function ih(a, b) {
      a.lanes |= b;
      var c = a.alternate;
      null !== c && (c.lanes |= b);
      c = a;
      for (a = a.return; null !== a; ) a.childLanes |= b, c = a.alternate, null !== c && (c.childLanes |= b), c = a, a = a.return;
      return 3 === c.tag ? c.stateNode : null;
    }
    var jh = false;
    function kh(a) {
      a.updateQueue = { baseState: a.memoizedState, firstBaseUpdate: null, lastBaseUpdate: null, shared: { pending: null, interleaved: null, lanes: 0 }, effects: null };
    }
    function lh(a, b) {
      a = a.updateQueue;
      b.updateQueue === a && (b.updateQueue = { baseState: a.baseState, firstBaseUpdate: a.firstBaseUpdate, lastBaseUpdate: a.lastBaseUpdate, shared: a.shared, effects: a.effects });
    }
    function mh(a, b) {
      return { eventTime: a, lane: b, tag: 0, payload: null, callback: null, next: null };
    }
    function nh(a, b, c) {
      var d = a.updateQueue;
      if (null === d) return null;
      d = d.shared;
      if (0 !== (K & 2)) {
        var e = d.pending;
        null === e ? b.next = b : (b.next = e.next, e.next = b);
        d.pending = b;
        return ih(a, c);
      }
      e = d.interleaved;
      null === e ? (b.next = b, gh(d)) : (b.next = e.next, e.next = b);
      d.interleaved = b;
      return ih(a, c);
    }
    function oh(a, b, c) {
      b = b.updateQueue;
      if (null !== b && (b = b.shared, 0 !== (c & 4194240))) {
        var d = b.lanes;
        d &= a.pendingLanes;
        c |= d;
        b.lanes = c;
        Cc(a, c);
      }
    }
    function ph(a, b) {
      var c = a.updateQueue, d = a.alternate;
      if (null !== d && (d = d.updateQueue, c === d)) {
        var e = null, f = null;
        c = c.firstBaseUpdate;
        if (null !== c) {
          do {
            var g = { eventTime: c.eventTime, lane: c.lane, tag: c.tag, payload: c.payload, callback: c.callback, next: null };
            null === f ? e = f = g : f = f.next = g;
            c = c.next;
          } while (null !== c);
          null === f ? e = f = b : f = f.next = b;
        } else e = f = b;
        c = { baseState: d.baseState, firstBaseUpdate: e, lastBaseUpdate: f, shared: d.shared, effects: d.effects };
        a.updateQueue = c;
        return;
      }
      a = c.lastBaseUpdate;
      null === a ? c.firstBaseUpdate = b : a.next = b;
      c.lastBaseUpdate = b;
    }
    function qh(a, b, c, d) {
      var e = a.updateQueue;
      jh = false;
      var f = e.firstBaseUpdate, g = e.lastBaseUpdate, h = e.shared.pending;
      if (null !== h) {
        e.shared.pending = null;
        var k = h, l = k.next;
        k.next = null;
        null === g ? f = l : g.next = l;
        g = k;
        var m = a.alternate;
        null !== m && (m = m.updateQueue, h = m.lastBaseUpdate, h !== g && (null === h ? m.firstBaseUpdate = l : h.next = l, m.lastBaseUpdate = k));
      }
      if (null !== f) {
        var q = e.baseState;
        g = 0;
        m = l = k = null;
        h = f;
        do {
          var r = h.lane, y = h.eventTime;
          if ((d & r) === r) {
            null !== m && (m = m.next = {
              eventTime: y,
              lane: 0,
              tag: h.tag,
              payload: h.payload,
              callback: h.callback,
              next: null
            });
            a: {
              var n = a, t = h;
              r = b;
              y = c;
              switch (t.tag) {
                case 1:
                  n = t.payload;
                  if ("function" === typeof n) {
                    q = n.call(y, q, r);
                    break a;
                  }
                  q = n;
                  break a;
                case 3:
                  n.flags = n.flags & -65537 | 128;
                case 0:
                  n = t.payload;
                  r = "function" === typeof n ? n.call(y, q, r) : n;
                  if (null === r || void 0 === r) break a;
                  q = A({}, q, r);
                  break a;
                case 2:
                  jh = true;
              }
            }
            null !== h.callback && 0 !== h.lane && (a.flags |= 64, r = e.effects, null === r ? e.effects = [h] : r.push(h));
          } else y = { eventTime: y, lane: r, tag: h.tag, payload: h.payload, callback: h.callback, next: null }, null === m ? (l = m = y, k = q) : m = m.next = y, g |= r;
          h = h.next;
          if (null === h) if (h = e.shared.pending, null === h) break;
          else r = h, h = r.next, r.next = null, e.lastBaseUpdate = r, e.shared.pending = null;
        } while (1);
        null === m && (k = q);
        e.baseState = k;
        e.firstBaseUpdate = l;
        e.lastBaseUpdate = m;
        b = e.shared.interleaved;
        if (null !== b) {
          e = b;
          do
            g |= e.lane, e = e.next;
          while (e !== b);
        } else null === f && (e.shared.lanes = 0);
        rh |= g;
        a.lanes = g;
        a.memoizedState = q;
      }
    }
    function sh(a, b, c) {
      a = b.effects;
      b.effects = null;
      if (null !== a) for (b = 0; b < a.length; b++) {
        var d = a[b], e = d.callback;
        if (null !== e) {
          d.callback = null;
          d = c;
          if ("function" !== typeof e) throw Error(p(191, e));
          e.call(d);
        }
      }
    }
    var th = {}, uh = Uf(th), vh = Uf(th), wh = Uf(th);
    function xh(a) {
      if (a === th) throw Error(p(174));
      return a;
    }
    function yh(a, b) {
      G(wh, b);
      G(vh, a);
      G(uh, th);
      a = b.nodeType;
      switch (a) {
        case 9:
        case 11:
          b = (b = b.documentElement) ? b.namespaceURI : lb(null, "");
          break;
        default:
          a = 8 === a ? b.parentNode : b, b = a.namespaceURI || null, a = a.tagName, b = lb(b, a);
      }
      E(uh);
      G(uh, b);
    }
    function zh() {
      E(uh);
      E(vh);
      E(wh);
    }
    function Ah(a) {
      xh(wh.current);
      var b = xh(uh.current);
      var c = lb(b, a.type);
      b !== c && (G(vh, a), G(uh, c));
    }
    function Bh(a) {
      vh.current === a && (E(uh), E(vh));
    }
    var L = Uf(0);
    function Ch(a) {
      for (var b = a; null !== b; ) {
        if (13 === b.tag) {
          var c = b.memoizedState;
          if (null !== c && (c = c.dehydrated, null === c || "$?" === c.data || "$!" === c.data)) return b;
        } else if (19 === b.tag && void 0 !== b.memoizedProps.revealOrder) {
          if (0 !== (b.flags & 128)) return b;
        } else if (null !== b.child) {
          b.child.return = b;
          b = b.child;
          continue;
        }
        if (b === a) break;
        for (; null === b.sibling; ) {
          if (null === b.return || b.return === a) return null;
          b = b.return;
        }
        b.sibling.return = b.return;
        b = b.sibling;
      }
      return null;
    }
    var Dh = [];
    function Eh() {
      for (var a = 0; a < Dh.length; a++) Dh[a]._workInProgressVersionPrimary = null;
      Dh.length = 0;
    }
    var Fh = ua.ReactCurrentDispatcher, Gh = ua.ReactCurrentBatchConfig, Hh = 0, M = null, N = null, O = null, Ih = false, Jh = false, Kh = 0, Lh = 0;
    function P() {
      throw Error(p(321));
    }
    function Mh(a, b) {
      if (null === b) return false;
      for (var c = 0; c < b.length && c < a.length; c++) if (!He(a[c], b[c])) return false;
      return true;
    }
    function Nh(a, b, c, d, e, f) {
      Hh = f;
      M = b;
      b.memoizedState = null;
      b.updateQueue = null;
      b.lanes = 0;
      Fh.current = null === a || null === a.memoizedState ? Oh : Ph;
      a = c(d, e);
      if (Jh) {
        f = 0;
        do {
          Jh = false;
          Kh = 0;
          if (25 <= f) throw Error(p(301));
          f += 1;
          O = N = null;
          b.updateQueue = null;
          Fh.current = Qh;
          a = c(d, e);
        } while (Jh);
      }
      Fh.current = Rh;
      b = null !== N && null !== N.next;
      Hh = 0;
      O = N = M = null;
      Ih = false;
      if (b) throw Error(p(300));
      return a;
    }
    function Sh() {
      var a = 0 !== Kh;
      Kh = 0;
      return a;
    }
    function Th() {
      var a = { memoizedState: null, baseState: null, baseQueue: null, queue: null, next: null };
      null === O ? M.memoizedState = O = a : O = O.next = a;
      return O;
    }
    function Uh() {
      if (null === N) {
        var a = M.alternate;
        a = null !== a ? a.memoizedState : null;
      } else a = N.next;
      var b = null === O ? M.memoizedState : O.next;
      if (null !== b) O = b, N = a;
      else {
        if (null === a) throw Error(p(310));
        N = a;
        a = { memoizedState: N.memoizedState, baseState: N.baseState, baseQueue: N.baseQueue, queue: N.queue, next: null };
        null === O ? M.memoizedState = O = a : O = O.next = a;
      }
      return O;
    }
    function Vh(a, b) {
      return "function" === typeof b ? b(a) : b;
    }
    function Wh(a) {
      var b = Uh(), c = b.queue;
      if (null === c) throw Error(p(311));
      c.lastRenderedReducer = a;
      var d = N, e = d.baseQueue, f = c.pending;
      if (null !== f) {
        if (null !== e) {
          var g = e.next;
          e.next = f.next;
          f.next = g;
        }
        d.baseQueue = e = f;
        c.pending = null;
      }
      if (null !== e) {
        f = e.next;
        d = d.baseState;
        var h = g = null, k = null, l = f;
        do {
          var m = l.lane;
          if ((Hh & m) === m) null !== k && (k = k.next = { lane: 0, action: l.action, hasEagerState: l.hasEagerState, eagerState: l.eagerState, next: null }), d = l.hasEagerState ? l.eagerState : a(d, l.action);
          else {
            var q = {
              lane: m,
              action: l.action,
              hasEagerState: l.hasEagerState,
              eagerState: l.eagerState,
              next: null
            };
            null === k ? (h = k = q, g = d) : k = k.next = q;
            M.lanes |= m;
            rh |= m;
          }
          l = l.next;
        } while (null !== l && l !== f);
        null === k ? g = d : k.next = h;
        He(d, b.memoizedState) || (dh = true);
        b.memoizedState = d;
        b.baseState = g;
        b.baseQueue = k;
        c.lastRenderedState = d;
      }
      a = c.interleaved;
      if (null !== a) {
        e = a;
        do
          f = e.lane, M.lanes |= f, rh |= f, e = e.next;
        while (e !== a);
      } else null === e && (c.lanes = 0);
      return [b.memoizedState, c.dispatch];
    }
    function Xh(a) {
      var b = Uh(), c = b.queue;
      if (null === c) throw Error(p(311));
      c.lastRenderedReducer = a;
      var d = c.dispatch, e = c.pending, f = b.memoizedState;
      if (null !== e) {
        c.pending = null;
        var g = e = e.next;
        do
          f = a(f, g.action), g = g.next;
        while (g !== e);
        He(f, b.memoizedState) || (dh = true);
        b.memoizedState = f;
        null === b.baseQueue && (b.baseState = f);
        c.lastRenderedState = f;
      }
      return [f, d];
    }
    function Yh() {
    }
    function Zh(a, b) {
      var c = M, d = Uh(), e = b(), f = !He(d.memoizedState, e);
      f && (d.memoizedState = e, dh = true);
      d = d.queue;
      $h(ai.bind(null, c, d, a), [a]);
      if (d.getSnapshot !== b || f || null !== O && O.memoizedState.tag & 1) {
        c.flags |= 2048;
        bi(9, ci.bind(null, c, d, e, b), void 0, null);
        if (null === Q) throw Error(p(349));
        0 !== (Hh & 30) || di(c, b, e);
      }
      return e;
    }
    function di(a, b, c) {
      a.flags |= 16384;
      a = { getSnapshot: b, value: c };
      b = M.updateQueue;
      null === b ? (b = { lastEffect: null, stores: null }, M.updateQueue = b, b.stores = [a]) : (c = b.stores, null === c ? b.stores = [a] : c.push(a));
    }
    function ci(a, b, c, d) {
      b.value = c;
      b.getSnapshot = d;
      ei(b) && fi(a);
    }
    function ai(a, b, c) {
      return c(function() {
        ei(b) && fi(a);
      });
    }
    function ei(a) {
      var b = a.getSnapshot;
      a = a.value;
      try {
        var c = b();
        return !He(a, c);
      } catch (d) {
        return true;
      }
    }
    function fi(a) {
      var b = ih(a, 1);
      null !== b && gi(b, a, 1, -1);
    }
    function hi(a) {
      var b = Th();
      "function" === typeof a && (a = a());
      b.memoizedState = b.baseState = a;
      a = { pending: null, interleaved: null, lanes: 0, dispatch: null, lastRenderedReducer: Vh, lastRenderedState: a };
      b.queue = a;
      a = a.dispatch = ii.bind(null, M, a);
      return [b.memoizedState, a];
    }
    function bi(a, b, c, d) {
      a = { tag: a, create: b, destroy: c, deps: d, next: null };
      b = M.updateQueue;
      null === b ? (b = { lastEffect: null, stores: null }, M.updateQueue = b, b.lastEffect = a.next = a) : (c = b.lastEffect, null === c ? b.lastEffect = a.next = a : (d = c.next, c.next = a, a.next = d, b.lastEffect = a));
      return a;
    }
    function ji() {
      return Uh().memoizedState;
    }
    function ki(a, b, c, d) {
      var e = Th();
      M.flags |= a;
      e.memoizedState = bi(1 | b, c, void 0, void 0 === d ? null : d);
    }
    function li(a, b, c, d) {
      var e = Uh();
      d = void 0 === d ? null : d;
      var f = void 0;
      if (null !== N) {
        var g = N.memoizedState;
        f = g.destroy;
        if (null !== d && Mh(d, g.deps)) {
          e.memoizedState = bi(b, c, f, d);
          return;
        }
      }
      M.flags |= a;
      e.memoizedState = bi(1 | b, c, f, d);
    }
    function mi(a, b) {
      return ki(8390656, 8, a, b);
    }
    function $h(a, b) {
      return li(2048, 8, a, b);
    }
    function ni(a, b) {
      return li(4, 2, a, b);
    }
    function oi(a, b) {
      return li(4, 4, a, b);
    }
    function pi(a, b) {
      if ("function" === typeof b) return a = a(), b(a), function() {
        b(null);
      };
      if (null !== b && void 0 !== b) return a = a(), b.current = a, function() {
        b.current = null;
      };
    }
    function qi(a, b, c) {
      c = null !== c && void 0 !== c ? c.concat([a]) : null;
      return li(4, 4, pi.bind(null, b, a), c);
    }
    function ri() {
    }
    function si(a, b) {
      var c = Uh();
      b = void 0 === b ? null : b;
      var d = c.memoizedState;
      if (null !== d && null !== b && Mh(b, d[1])) return d[0];
      c.memoizedState = [a, b];
      return a;
    }
    function ti(a, b) {
      var c = Uh();
      b = void 0 === b ? null : b;
      var d = c.memoizedState;
      if (null !== d && null !== b && Mh(b, d[1])) return d[0];
      a = a();
      c.memoizedState = [a, b];
      return a;
    }
    function ui(a, b, c) {
      if (0 === (Hh & 21)) return a.baseState && (a.baseState = false, dh = true), a.memoizedState = c;
      He(c, b) || (c = yc(), M.lanes |= c, rh |= c, a.baseState = true);
      return b;
    }
    function vi(a, b) {
      var c = C;
      C = 0 !== c && 4 > c ? c : 4;
      a(true);
      var d = Gh.transition;
      Gh.transition = {};
      try {
        a(false), b();
      } finally {
        C = c, Gh.transition = d;
      }
    }
    function wi() {
      return Uh().memoizedState;
    }
    function xi(a, b, c) {
      var d = yi(a);
      c = { lane: d, action: c, hasEagerState: false, eagerState: null, next: null };
      if (zi(a)) Ai(b, c);
      else if (c = hh(a, b, c, d), null !== c) {
        var e = R();
        gi(c, a, d, e);
        Bi(c, b, d);
      }
    }
    function ii(a, b, c) {
      var d = yi(a), e = { lane: d, action: c, hasEagerState: false, eagerState: null, next: null };
      if (zi(a)) Ai(b, e);
      else {
        var f = a.alternate;
        if (0 === a.lanes && (null === f || 0 === f.lanes) && (f = b.lastRenderedReducer, null !== f)) try {
          var g = b.lastRenderedState, h = f(g, c);
          e.hasEagerState = true;
          e.eagerState = h;
          if (He(h, g)) {
            var k = b.interleaved;
            null === k ? (e.next = e, gh(b)) : (e.next = k.next, k.next = e);
            b.interleaved = e;
            return;
          }
        } catch (l) {
        } finally {
        }
        c = hh(a, b, e, d);
        null !== c && (e = R(), gi(c, a, d, e), Bi(c, b, d));
      }
    }
    function zi(a) {
      var b = a.alternate;
      return a === M || null !== b && b === M;
    }
    function Ai(a, b) {
      Jh = Ih = true;
      var c = a.pending;
      null === c ? b.next = b : (b.next = c.next, c.next = b);
      a.pending = b;
    }
    function Bi(a, b, c) {
      if (0 !== (c & 4194240)) {
        var d = b.lanes;
        d &= a.pendingLanes;
        c |= d;
        b.lanes = c;
        Cc(a, c);
      }
    }
    var Rh = { readContext: eh, useCallback: P, useContext: P, useEffect: P, useImperativeHandle: P, useInsertionEffect: P, useLayoutEffect: P, useMemo: P, useReducer: P, useRef: P, useState: P, useDebugValue: P, useDeferredValue: P, useTransition: P, useMutableSource: P, useSyncExternalStore: P, useId: P, unstable_isNewReconciler: false }, Oh = { readContext: eh, useCallback: function(a, b) {
      Th().memoizedState = [a, void 0 === b ? null : b];
      return a;
    }, useContext: eh, useEffect: mi, useImperativeHandle: function(a, b, c) {
      c = null !== c && void 0 !== c ? c.concat([a]) : null;
      return ki(
        4194308,
        4,
        pi.bind(null, b, a),
        c
      );
    }, useLayoutEffect: function(a, b) {
      return ki(4194308, 4, a, b);
    }, useInsertionEffect: function(a, b) {
      return ki(4, 2, a, b);
    }, useMemo: function(a, b) {
      var c = Th();
      b = void 0 === b ? null : b;
      a = a();
      c.memoizedState = [a, b];
      return a;
    }, useReducer: function(a, b, c) {
      var d = Th();
      b = void 0 !== c ? c(b) : b;
      d.memoizedState = d.baseState = b;
      a = { pending: null, interleaved: null, lanes: 0, dispatch: null, lastRenderedReducer: a, lastRenderedState: b };
      d.queue = a;
      a = a.dispatch = xi.bind(null, M, a);
      return [d.memoizedState, a];
    }, useRef: function(a) {
      var b = Th();
      a = { current: a };
      return b.memoizedState = a;
    }, useState: hi, useDebugValue: ri, useDeferredValue: function(a) {
      return Th().memoizedState = a;
    }, useTransition: function() {
      var a = hi(false), b = a[0];
      a = vi.bind(null, a[1]);
      Th().memoizedState = a;
      return [b, a];
    }, useMutableSource: function() {
    }, useSyncExternalStore: function(a, b, c) {
      var d = M, e = Th();
      if (I) {
        if (void 0 === c) throw Error(p(407));
        c = c();
      } else {
        c = b();
        if (null === Q) throw Error(p(349));
        0 !== (Hh & 30) || di(d, b, c);
      }
      e.memoizedState = c;
      var f = { value: c, getSnapshot: b };
      e.queue = f;
      mi(ai.bind(
        null,
        d,
        f,
        a
      ), [a]);
      d.flags |= 2048;
      bi(9, ci.bind(null, d, f, c, b), void 0, null);
      return c;
    }, useId: function() {
      var a = Th(), b = Q.identifierPrefix;
      if (I) {
        var c = sg;
        var d = rg;
        c = (d & ~(1 << 32 - oc(d) - 1)).toString(32) + c;
        b = ":" + b + "R" + c;
        c = Kh++;
        0 < c && (b += "H" + c.toString(32));
        b += ":";
      } else c = Lh++, b = ":" + b + "r" + c.toString(32) + ":";
      return a.memoizedState = b;
    }, unstable_isNewReconciler: false }, Ph = {
      readContext: eh,
      useCallback: si,
      useContext: eh,
      useEffect: $h,
      useImperativeHandle: qi,
      useInsertionEffect: ni,
      useLayoutEffect: oi,
      useMemo: ti,
      useReducer: Wh,
      useRef: ji,
      useState: function() {
        return Wh(Vh);
      },
      useDebugValue: ri,
      useDeferredValue: function(a) {
        var b = Uh();
        return ui(b, N.memoizedState, a);
      },
      useTransition: function() {
        var a = Wh(Vh)[0], b = Uh().memoizedState;
        return [a, b];
      },
      useMutableSource: Yh,
      useSyncExternalStore: Zh,
      useId: wi,
      unstable_isNewReconciler: false
    }, Qh = { readContext: eh, useCallback: si, useContext: eh, useEffect: $h, useImperativeHandle: qi, useInsertionEffect: ni, useLayoutEffect: oi, useMemo: ti, useReducer: Xh, useRef: ji, useState: function() {
      return Xh(Vh);
    }, useDebugValue: ri, useDeferredValue: function(a) {
      var b = Uh();
      return null === N ? b.memoizedState = a : ui(b, N.memoizedState, a);
    }, useTransition: function() {
      var a = Xh(Vh)[0], b = Uh().memoizedState;
      return [a, b];
    }, useMutableSource: Yh, useSyncExternalStore: Zh, useId: wi, unstable_isNewReconciler: false };
    function Ci(a, b) {
      if (a && a.defaultProps) {
        b = A({}, b);
        a = a.defaultProps;
        for (var c in a) void 0 === b[c] && (b[c] = a[c]);
        return b;
      }
      return b;
    }
    function Di(a, b, c, d) {
      b = a.memoizedState;
      c = c(d, b);
      c = null === c || void 0 === c ? b : A({}, b, c);
      a.memoizedState = c;
      0 === a.lanes && (a.updateQueue.baseState = c);
    }
    var Ei = { isMounted: function(a) {
      return (a = a._reactInternals) ? Vb(a) === a : false;
    }, enqueueSetState: function(a, b, c) {
      a = a._reactInternals;
      var d = R(), e = yi(a), f = mh(d, e);
      f.payload = b;
      void 0 !== c && null !== c && (f.callback = c);
      b = nh(a, f, e);
      null !== b && (gi(b, a, e, d), oh(b, a, e));
    }, enqueueReplaceState: function(a, b, c) {
      a = a._reactInternals;
      var d = R(), e = yi(a), f = mh(d, e);
      f.tag = 1;
      f.payload = b;
      void 0 !== c && null !== c && (f.callback = c);
      b = nh(a, f, e);
      null !== b && (gi(b, a, e, d), oh(b, a, e));
    }, enqueueForceUpdate: function(a, b) {
      a = a._reactInternals;
      var c = R(), d = yi(a), e = mh(c, d);
      e.tag = 2;
      void 0 !== b && null !== b && (e.callback = b);
      b = nh(a, e, d);
      null !== b && (gi(b, a, d, c), oh(b, a, d));
    } };
    function Fi(a, b, c, d, e, f, g) {
      a = a.stateNode;
      return "function" === typeof a.shouldComponentUpdate ? a.shouldComponentUpdate(d, f, g) : b.prototype && b.prototype.isPureReactComponent ? !Ie(c, d) || !Ie(e, f) : true;
    }
    function Gi(a, b, c) {
      var d = false, e = Vf;
      var f = b.contextType;
      "object" === typeof f && null !== f ? f = eh(f) : (e = Zf(b) ? Xf : H.current, d = b.contextTypes, f = (d = null !== d && void 0 !== d) ? Yf(a, e) : Vf);
      b = new b(c, f);
      a.memoizedState = null !== b.state && void 0 !== b.state ? b.state : null;
      b.updater = Ei;
      a.stateNode = b;
      b._reactInternals = a;
      d && (a = a.stateNode, a.__reactInternalMemoizedUnmaskedChildContext = e, a.__reactInternalMemoizedMaskedChildContext = f);
      return b;
    }
    function Hi(a, b, c, d) {
      a = b.state;
      "function" === typeof b.componentWillReceiveProps && b.componentWillReceiveProps(c, d);
      "function" === typeof b.UNSAFE_componentWillReceiveProps && b.UNSAFE_componentWillReceiveProps(c, d);
      b.state !== a && Ei.enqueueReplaceState(b, b.state, null);
    }
    function Ii(a, b, c, d) {
      var e = a.stateNode;
      e.props = c;
      e.state = a.memoizedState;
      e.refs = {};
      kh(a);
      var f = b.contextType;
      "object" === typeof f && null !== f ? e.context = eh(f) : (f = Zf(b) ? Xf : H.current, e.context = Yf(a, f));
      e.state = a.memoizedState;
      f = b.getDerivedStateFromProps;
      "function" === typeof f && (Di(a, b, f, c), e.state = a.memoizedState);
      "function" === typeof b.getDerivedStateFromProps || "function" === typeof e.getSnapshotBeforeUpdate || "function" !== typeof e.UNSAFE_componentWillMount && "function" !== typeof e.componentWillMount || (b = e.state, "function" === typeof e.componentWillMount && e.componentWillMount(), "function" === typeof e.UNSAFE_componentWillMount && e.UNSAFE_componentWillMount(), b !== e.state && Ei.enqueueReplaceState(e, e.state, null), qh(a, c, e, d), e.state = a.memoizedState);
      "function" === typeof e.componentDidMount && (a.flags |= 4194308);
    }
    function Ji(a, b) {
      try {
        var c = "", d = b;
        do
          c += Pa(d), d = d.return;
        while (d);
        var e = c;
      } catch (f) {
        e = "\nError generating stack: " + f.message + "\n" + f.stack;
      }
      return { value: a, source: b, stack: e, digest: null };
    }
    function Ki(a, b, c) {
      return { value: a, source: null, stack: null != c ? c : null, digest: null != b ? b : null };
    }
    function Li(a, b) {
      try {
        console.error(b.value);
      } catch (c) {
        setTimeout(function() {
          throw c;
        });
      }
    }
    var Mi = "function" === typeof WeakMap ? WeakMap : Map;
    function Ni(a, b, c) {
      c = mh(-1, c);
      c.tag = 3;
      c.payload = { element: null };
      var d = b.value;
      c.callback = function() {
        Oi || (Oi = true, Pi = d);
        Li(a, b);
      };
      return c;
    }
    function Qi(a, b, c) {
      c = mh(-1, c);
      c.tag = 3;
      var d = a.type.getDerivedStateFromError;
      if ("function" === typeof d) {
        var e = b.value;
        c.payload = function() {
          return d(e);
        };
        c.callback = function() {
          Li(a, b);
        };
      }
      var f = a.stateNode;
      null !== f && "function" === typeof f.componentDidCatch && (c.callback = function() {
        Li(a, b);
        "function" !== typeof d && (null === Ri ? Ri = /* @__PURE__ */ new Set([this]) : Ri.add(this));
        var c2 = b.stack;
        this.componentDidCatch(b.value, { componentStack: null !== c2 ? c2 : "" });
      });
      return c;
    }
    function Si(a, b, c) {
      var d = a.pingCache;
      if (null === d) {
        d = a.pingCache = new Mi();
        var e = /* @__PURE__ */ new Set();
        d.set(b, e);
      } else e = d.get(b), void 0 === e && (e = /* @__PURE__ */ new Set(), d.set(b, e));
      e.has(c) || (e.add(c), a = Ti.bind(null, a, b, c), b.then(a, a));
    }
    function Ui(a) {
      do {
        var b;
        if (b = 13 === a.tag) b = a.memoizedState, b = null !== b ? null !== b.dehydrated ? true : false : true;
        if (b) return a;
        a = a.return;
      } while (null !== a);
      return null;
    }
    function Vi(a, b, c, d, e) {
      if (0 === (a.mode & 1)) return a === b ? a.flags |= 65536 : (a.flags |= 128, c.flags |= 131072, c.flags &= -52805, 1 === c.tag && (null === c.alternate ? c.tag = 17 : (b = mh(-1, 1), b.tag = 2, nh(c, b, 1))), c.lanes |= 1), a;
      a.flags |= 65536;
      a.lanes = e;
      return a;
    }
    var Wi = ua.ReactCurrentOwner, dh = false;
    function Xi(a, b, c, d) {
      b.child = null === a ? Vg(b, null, c, d) : Ug(b, a.child, c, d);
    }
    function Yi(a, b, c, d, e) {
      c = c.render;
      var f = b.ref;
      ch(b, e);
      d = Nh(a, b, c, d, f, e);
      c = Sh();
      if (null !== a && !dh) return b.updateQueue = a.updateQueue, b.flags &= -2053, a.lanes &= ~e, Zi(a, b, e);
      I && c && vg(b);
      b.flags |= 1;
      Xi(a, b, d, e);
      return b.child;
    }
    function $i(a, b, c, d, e) {
      if (null === a) {
        var f = c.type;
        if ("function" === typeof f && !aj(f) && void 0 === f.defaultProps && null === c.compare && void 0 === c.defaultProps) return b.tag = 15, b.type = f, bj(a, b, f, d, e);
        a = Rg(c.type, null, d, b, b.mode, e);
        a.ref = b.ref;
        a.return = b;
        return b.child = a;
      }
      f = a.child;
      if (0 === (a.lanes & e)) {
        var g = f.memoizedProps;
        c = c.compare;
        c = null !== c ? c : Ie;
        if (c(g, d) && a.ref === b.ref) return Zi(a, b, e);
      }
      b.flags |= 1;
      a = Pg(f, d);
      a.ref = b.ref;
      a.return = b;
      return b.child = a;
    }
    function bj(a, b, c, d, e) {
      if (null !== a) {
        var f = a.memoizedProps;
        if (Ie(f, d) && a.ref === b.ref) if (dh = false, b.pendingProps = d = f, 0 !== (a.lanes & e)) 0 !== (a.flags & 131072) && (dh = true);
        else return b.lanes = a.lanes, Zi(a, b, e);
      }
      return cj(a, b, c, d, e);
    }
    function dj(a, b, c) {
      var d = b.pendingProps, e = d.children, f = null !== a ? a.memoizedState : null;
      if ("hidden" === d.mode) if (0 === (b.mode & 1)) b.memoizedState = { baseLanes: 0, cachePool: null, transitions: null }, G(ej, fj), fj |= c;
      else {
        if (0 === (c & 1073741824)) return a = null !== f ? f.baseLanes | c : c, b.lanes = b.childLanes = 1073741824, b.memoizedState = { baseLanes: a, cachePool: null, transitions: null }, b.updateQueue = null, G(ej, fj), fj |= a, null;
        b.memoizedState = { baseLanes: 0, cachePool: null, transitions: null };
        d = null !== f ? f.baseLanes : c;
        G(ej, fj);
        fj |= d;
      }
      else null !== f ? (d = f.baseLanes | c, b.memoizedState = null) : d = c, G(ej, fj), fj |= d;
      Xi(a, b, e, c);
      return b.child;
    }
    function gj(a, b) {
      var c = b.ref;
      if (null === a && null !== c || null !== a && a.ref !== c) b.flags |= 512, b.flags |= 2097152;
    }
    function cj(a, b, c, d, e) {
      var f = Zf(c) ? Xf : H.current;
      f = Yf(b, f);
      ch(b, e);
      c = Nh(a, b, c, d, f, e);
      d = Sh();
      if (null !== a && !dh) return b.updateQueue = a.updateQueue, b.flags &= -2053, a.lanes &= ~e, Zi(a, b, e);
      I && d && vg(b);
      b.flags |= 1;
      Xi(a, b, c, e);
      return b.child;
    }
    function hj(a, b, c, d, e) {
      if (Zf(c)) {
        var f = true;
        cg(b);
      } else f = false;
      ch(b, e);
      if (null === b.stateNode) ij(a, b), Gi(b, c, d), Ii(b, c, d, e), d = true;
      else if (null === a) {
        var g = b.stateNode, h = b.memoizedProps;
        g.props = h;
        var k = g.context, l = c.contextType;
        "object" === typeof l && null !== l ? l = eh(l) : (l = Zf(c) ? Xf : H.current, l = Yf(b, l));
        var m = c.getDerivedStateFromProps, q = "function" === typeof m || "function" === typeof g.getSnapshotBeforeUpdate;
        q || "function" !== typeof g.UNSAFE_componentWillReceiveProps && "function" !== typeof g.componentWillReceiveProps || (h !== d || k !== l) && Hi(b, g, d, l);
        jh = false;
        var r = b.memoizedState;
        g.state = r;
        qh(b, d, g, e);
        k = b.memoizedState;
        h !== d || r !== k || Wf.current || jh ? ("function" === typeof m && (Di(b, c, m, d), k = b.memoizedState), (h = jh || Fi(b, c, h, d, r, k, l)) ? (q || "function" !== typeof g.UNSAFE_componentWillMount && "function" !== typeof g.componentWillMount || ("function" === typeof g.componentWillMount && g.componentWillMount(), "function" === typeof g.UNSAFE_componentWillMount && g.UNSAFE_componentWillMount()), "function" === typeof g.componentDidMount && (b.flags |= 4194308)) : ("function" === typeof g.componentDidMount && (b.flags |= 4194308), b.memoizedProps = d, b.memoizedState = k), g.props = d, g.state = k, g.context = l, d = h) : ("function" === typeof g.componentDidMount && (b.flags |= 4194308), d = false);
      } else {
        g = b.stateNode;
        lh(a, b);
        h = b.memoizedProps;
        l = b.type === b.elementType ? h : Ci(b.type, h);
        g.props = l;
        q = b.pendingProps;
        r = g.context;
        k = c.contextType;
        "object" === typeof k && null !== k ? k = eh(k) : (k = Zf(c) ? Xf : H.current, k = Yf(b, k));
        var y = c.getDerivedStateFromProps;
        (m = "function" === typeof y || "function" === typeof g.getSnapshotBeforeUpdate) || "function" !== typeof g.UNSAFE_componentWillReceiveProps && "function" !== typeof g.componentWillReceiveProps || (h !== q || r !== k) && Hi(b, g, d, k);
        jh = false;
        r = b.memoizedState;
        g.state = r;
        qh(b, d, g, e);
        var n = b.memoizedState;
        h !== q || r !== n || Wf.current || jh ? ("function" === typeof y && (Di(b, c, y, d), n = b.memoizedState), (l = jh || Fi(b, c, l, d, r, n, k) || false) ? (m || "function" !== typeof g.UNSAFE_componentWillUpdate && "function" !== typeof g.componentWillUpdate || ("function" === typeof g.componentWillUpdate && g.componentWillUpdate(d, n, k), "function" === typeof g.UNSAFE_componentWillUpdate && g.UNSAFE_componentWillUpdate(d, n, k)), "function" === typeof g.componentDidUpdate && (b.flags |= 4), "function" === typeof g.getSnapshotBeforeUpdate && (b.flags |= 1024)) : ("function" !== typeof g.componentDidUpdate || h === a.memoizedProps && r === a.memoizedState || (b.flags |= 4), "function" !== typeof g.getSnapshotBeforeUpdate || h === a.memoizedProps && r === a.memoizedState || (b.flags |= 1024), b.memoizedProps = d, b.memoizedState = n), g.props = d, g.state = n, g.context = k, d = l) : ("function" !== typeof g.componentDidUpdate || h === a.memoizedProps && r === a.memoizedState || (b.flags |= 4), "function" !== typeof g.getSnapshotBeforeUpdate || h === a.memoizedProps && r === a.memoizedState || (b.flags |= 1024), d = false);
      }
      return jj(a, b, c, d, f, e);
    }
    function jj(a, b, c, d, e, f) {
      gj(a, b);
      var g = 0 !== (b.flags & 128);
      if (!d && !g) return e && dg(b, c, false), Zi(a, b, f);
      d = b.stateNode;
      Wi.current = b;
      var h = g && "function" !== typeof c.getDerivedStateFromError ? null : d.render();
      b.flags |= 1;
      null !== a && g ? (b.child = Ug(b, a.child, null, f), b.child = Ug(b, null, h, f)) : Xi(a, b, h, f);
      b.memoizedState = d.state;
      e && dg(b, c, true);
      return b.child;
    }
    function kj(a) {
      var b = a.stateNode;
      b.pendingContext ? ag(a, b.pendingContext, b.pendingContext !== b.context) : b.context && ag(a, b.context, false);
      yh(a, b.containerInfo);
    }
    function lj(a, b, c, d, e) {
      Ig();
      Jg(e);
      b.flags |= 256;
      Xi(a, b, c, d);
      return b.child;
    }
    var mj = { dehydrated: null, treeContext: null, retryLane: 0 };
    function nj(a) {
      return { baseLanes: a, cachePool: null, transitions: null };
    }
    function oj(a, b, c) {
      var d = b.pendingProps, e = L.current, f = false, g = 0 !== (b.flags & 128), h;
      (h = g) || (h = null !== a && null === a.memoizedState ? false : 0 !== (e & 2));
      if (h) f = true, b.flags &= -129;
      else if (null === a || null !== a.memoizedState) e |= 1;
      G(L, e & 1);
      if (null === a) {
        Eg(b);
        a = b.memoizedState;
        if (null !== a && (a = a.dehydrated, null !== a)) return 0 === (b.mode & 1) ? b.lanes = 1 : "$!" === a.data ? b.lanes = 8 : b.lanes = 1073741824, null;
        g = d.children;
        a = d.fallback;
        return f ? (d = b.mode, f = b.child, g = { mode: "hidden", children: g }, 0 === (d & 1) && null !== f ? (f.childLanes = 0, f.pendingProps = g) : f = pj(g, d, 0, null), a = Tg(a, d, c, null), f.return = b, a.return = b, f.sibling = a, b.child = f, b.child.memoizedState = nj(c), b.memoizedState = mj, a) : qj(b, g);
      }
      e = a.memoizedState;
      if (null !== e && (h = e.dehydrated, null !== h)) return rj(a, b, g, d, h, e, c);
      if (f) {
        f = d.fallback;
        g = b.mode;
        e = a.child;
        h = e.sibling;
        var k = { mode: "hidden", children: d.children };
        0 === (g & 1) && b.child !== e ? (d = b.child, d.childLanes = 0, d.pendingProps = k, b.deletions = null) : (d = Pg(e, k), d.subtreeFlags = e.subtreeFlags & 14680064);
        null !== h ? f = Pg(h, f) : (f = Tg(f, g, c, null), f.flags |= 2);
        f.return = b;
        d.return = b;
        d.sibling = f;
        b.child = d;
        d = f;
        f = b.child;
        g = a.child.memoizedState;
        g = null === g ? nj(c) : { baseLanes: g.baseLanes | c, cachePool: null, transitions: g.transitions };
        f.memoizedState = g;
        f.childLanes = a.childLanes & ~c;
        b.memoizedState = mj;
        return d;
      }
      f = a.child;
      a = f.sibling;
      d = Pg(f, { mode: "visible", children: d.children });
      0 === (b.mode & 1) && (d.lanes = c);
      d.return = b;
      d.sibling = null;
      null !== a && (c = b.deletions, null === c ? (b.deletions = [a], b.flags |= 16) : c.push(a));
      b.child = d;
      b.memoizedState = null;
      return d;
    }
    function qj(a, b) {
      b = pj({ mode: "visible", children: b }, a.mode, 0, null);
      b.return = a;
      return a.child = b;
    }
    function sj(a, b, c, d) {
      null !== d && Jg(d);
      Ug(b, a.child, null, c);
      a = qj(b, b.pendingProps.children);
      a.flags |= 2;
      b.memoizedState = null;
      return a;
    }
    function rj(a, b, c, d, e, f, g) {
      if (c) {
        if (b.flags & 256) return b.flags &= -257, d = Ki(Error(p(422))), sj(a, b, g, d);
        if (null !== b.memoizedState) return b.child = a.child, b.flags |= 128, null;
        f = d.fallback;
        e = b.mode;
        d = pj({ mode: "visible", children: d.children }, e, 0, null);
        f = Tg(f, e, g, null);
        f.flags |= 2;
        d.return = b;
        f.return = b;
        d.sibling = f;
        b.child = d;
        0 !== (b.mode & 1) && Ug(b, a.child, null, g);
        b.child.memoizedState = nj(g);
        b.memoizedState = mj;
        return f;
      }
      if (0 === (b.mode & 1)) return sj(a, b, g, null);
      if ("$!" === e.data) {
        d = e.nextSibling && e.nextSibling.dataset;
        if (d) var h = d.dgst;
        d = h;
        f = Error(p(419));
        d = Ki(f, d, void 0);
        return sj(a, b, g, d);
      }
      h = 0 !== (g & a.childLanes);
      if (dh || h) {
        d = Q;
        if (null !== d) {
          switch (g & -g) {
            case 4:
              e = 2;
              break;
            case 16:
              e = 8;
              break;
            case 64:
            case 128:
            case 256:
            case 512:
            case 1024:
            case 2048:
            case 4096:
            case 8192:
            case 16384:
            case 32768:
            case 65536:
            case 131072:
            case 262144:
            case 524288:
            case 1048576:
            case 2097152:
            case 4194304:
            case 8388608:
            case 16777216:
            case 33554432:
            case 67108864:
              e = 32;
              break;
            case 536870912:
              e = 268435456;
              break;
            default:
              e = 0;
          }
          e = 0 !== (e & (d.suspendedLanes | g)) ? 0 : e;
          0 !== e && e !== f.retryLane && (f.retryLane = e, ih(a, e), gi(d, a, e, -1));
        }
        tj();
        d = Ki(Error(p(421)));
        return sj(a, b, g, d);
      }
      if ("$?" === e.data) return b.flags |= 128, b.child = a.child, b = uj.bind(null, a), e._reactRetry = b, null;
      a = f.treeContext;
      yg = Lf(e.nextSibling);
      xg = b;
      I = true;
      zg = null;
      null !== a && (og[pg++] = rg, og[pg++] = sg, og[pg++] = qg, rg = a.id, sg = a.overflow, qg = b);
      b = qj(b, d.children);
      b.flags |= 4096;
      return b;
    }
    function vj(a, b, c) {
      a.lanes |= b;
      var d = a.alternate;
      null !== d && (d.lanes |= b);
      bh(a.return, b, c);
    }
    function wj(a, b, c, d, e) {
      var f = a.memoizedState;
      null === f ? a.memoizedState = { isBackwards: b, rendering: null, renderingStartTime: 0, last: d, tail: c, tailMode: e } : (f.isBackwards = b, f.rendering = null, f.renderingStartTime = 0, f.last = d, f.tail = c, f.tailMode = e);
    }
    function xj(a, b, c) {
      var d = b.pendingProps, e = d.revealOrder, f = d.tail;
      Xi(a, b, d.children, c);
      d = L.current;
      if (0 !== (d & 2)) d = d & 1 | 2, b.flags |= 128;
      else {
        if (null !== a && 0 !== (a.flags & 128)) a: for (a = b.child; null !== a; ) {
          if (13 === a.tag) null !== a.memoizedState && vj(a, c, b);
          else if (19 === a.tag) vj(a, c, b);
          else if (null !== a.child) {
            a.child.return = a;
            a = a.child;
            continue;
          }
          if (a === b) break a;
          for (; null === a.sibling; ) {
            if (null === a.return || a.return === b) break a;
            a = a.return;
          }
          a.sibling.return = a.return;
          a = a.sibling;
        }
        d &= 1;
      }
      G(L, d);
      if (0 === (b.mode & 1)) b.memoizedState = null;
      else switch (e) {
        case "forwards":
          c = b.child;
          for (e = null; null !== c; ) a = c.alternate, null !== a && null === Ch(a) && (e = c), c = c.sibling;
          c = e;
          null === c ? (e = b.child, b.child = null) : (e = c.sibling, c.sibling = null);
          wj(b, false, e, c, f);
          break;
        case "backwards":
          c = null;
          e = b.child;
          for (b.child = null; null !== e; ) {
            a = e.alternate;
            if (null !== a && null === Ch(a)) {
              b.child = e;
              break;
            }
            a = e.sibling;
            e.sibling = c;
            c = e;
            e = a;
          }
          wj(b, true, c, null, f);
          break;
        case "together":
          wj(b, false, null, null, void 0);
          break;
        default:
          b.memoizedState = null;
      }
      return b.child;
    }
    function ij(a, b) {
      0 === (b.mode & 1) && null !== a && (a.alternate = null, b.alternate = null, b.flags |= 2);
    }
    function Zi(a, b, c) {
      null !== a && (b.dependencies = a.dependencies);
      rh |= b.lanes;
      if (0 === (c & b.childLanes)) return null;
      if (null !== a && b.child !== a.child) throw Error(p(153));
      if (null !== b.child) {
        a = b.child;
        c = Pg(a, a.pendingProps);
        b.child = c;
        for (c.return = b; null !== a.sibling; ) a = a.sibling, c = c.sibling = Pg(a, a.pendingProps), c.return = b;
        c.sibling = null;
      }
      return b.child;
    }
    function yj(a, b, c) {
      switch (b.tag) {
        case 3:
          kj(b);
          Ig();
          break;
        case 5:
          Ah(b);
          break;
        case 1:
          Zf(b.type) && cg(b);
          break;
        case 4:
          yh(b, b.stateNode.containerInfo);
          break;
        case 10:
          var d = b.type._context, e = b.memoizedProps.value;
          G(Wg, d._currentValue);
          d._currentValue = e;
          break;
        case 13:
          d = b.memoizedState;
          if (null !== d) {
            if (null !== d.dehydrated) return G(L, L.current & 1), b.flags |= 128, null;
            if (0 !== (c & b.child.childLanes)) return oj(a, b, c);
            G(L, L.current & 1);
            a = Zi(a, b, c);
            return null !== a ? a.sibling : null;
          }
          G(L, L.current & 1);
          break;
        case 19:
          d = 0 !== (c & b.childLanes);
          if (0 !== (a.flags & 128)) {
            if (d) return xj(a, b, c);
            b.flags |= 128;
          }
          e = b.memoizedState;
          null !== e && (e.rendering = null, e.tail = null, e.lastEffect = null);
          G(L, L.current);
          if (d) break;
          else return null;
        case 22:
        case 23:
          return b.lanes = 0, dj(a, b, c);
      }
      return Zi(a, b, c);
    }
    var zj, Aj, Bj, Cj;
    zj = function(a, b) {
      for (var c = b.child; null !== c; ) {
        if (5 === c.tag || 6 === c.tag) a.appendChild(c.stateNode);
        else if (4 !== c.tag && null !== c.child) {
          c.child.return = c;
          c = c.child;
          continue;
        }
        if (c === b) break;
        for (; null === c.sibling; ) {
          if (null === c.return || c.return === b) return;
          c = c.return;
        }
        c.sibling.return = c.return;
        c = c.sibling;
      }
    };
    Aj = function() {
    };
    Bj = function(a, b, c, d) {
      var e = a.memoizedProps;
      if (e !== d) {
        a = b.stateNode;
        xh(uh.current);
        var f = null;
        switch (c) {
          case "input":
            e = Ya(a, e);
            d = Ya(a, d);
            f = [];
            break;
          case "select":
            e = A({}, e, { value: void 0 });
            d = A({}, d, { value: void 0 });
            f = [];
            break;
          case "textarea":
            e = gb(a, e);
            d = gb(a, d);
            f = [];
            break;
          default:
            "function" !== typeof e.onClick && "function" === typeof d.onClick && (a.onclick = Bf);
        }
        ub(c, d);
        var g;
        c = null;
        for (l in e) if (!d.hasOwnProperty(l) && e.hasOwnProperty(l) && null != e[l]) if ("style" === l) {
          var h = e[l];
          for (g in h) h.hasOwnProperty(g) && (c || (c = {}), c[g] = "");
        } else "dangerouslySetInnerHTML" !== l && "children" !== l && "suppressContentEditableWarning" !== l && "suppressHydrationWarning" !== l && "autoFocus" !== l && (ea.hasOwnProperty(l) ? f || (f = []) : (f = f || []).push(l, null));
        for (l in d) {
          var k = d[l];
          h = null != e ? e[l] : void 0;
          if (d.hasOwnProperty(l) && k !== h && (null != k || null != h)) if ("style" === l) if (h) {
            for (g in h) !h.hasOwnProperty(g) || k && k.hasOwnProperty(g) || (c || (c = {}), c[g] = "");
            for (g in k) k.hasOwnProperty(g) && h[g] !== k[g] && (c || (c = {}), c[g] = k[g]);
          } else c || (f || (f = []), f.push(
            l,
            c
          )), c = k;
          else "dangerouslySetInnerHTML" === l ? (k = k ? k.__html : void 0, h = h ? h.__html : void 0, null != k && h !== k && (f = f || []).push(l, k)) : "children" === l ? "string" !== typeof k && "number" !== typeof k || (f = f || []).push(l, "" + k) : "suppressContentEditableWarning" !== l && "suppressHydrationWarning" !== l && (ea.hasOwnProperty(l) ? (null != k && "onScroll" === l && D("scroll", a), f || h === k || (f = [])) : (f = f || []).push(l, k));
        }
        c && (f = f || []).push("style", c);
        var l = f;
        if (b.updateQueue = l) b.flags |= 4;
      }
    };
    Cj = function(a, b, c, d) {
      c !== d && (b.flags |= 4);
    };
    function Dj(a, b) {
      if (!I) switch (a.tailMode) {
        case "hidden":
          b = a.tail;
          for (var c = null; null !== b; ) null !== b.alternate && (c = b), b = b.sibling;
          null === c ? a.tail = null : c.sibling = null;
          break;
        case "collapsed":
          c = a.tail;
          for (var d = null; null !== c; ) null !== c.alternate && (d = c), c = c.sibling;
          null === d ? b || null === a.tail ? a.tail = null : a.tail.sibling = null : d.sibling = null;
      }
    }
    function S(a) {
      var b = null !== a.alternate && a.alternate.child === a.child, c = 0, d = 0;
      if (b) for (var e = a.child; null !== e; ) c |= e.lanes | e.childLanes, d |= e.subtreeFlags & 14680064, d |= e.flags & 14680064, e.return = a, e = e.sibling;
      else for (e = a.child; null !== e; ) c |= e.lanes | e.childLanes, d |= e.subtreeFlags, d |= e.flags, e.return = a, e = e.sibling;
      a.subtreeFlags |= d;
      a.childLanes = c;
      return b;
    }
    function Ej(a, b, c) {
      var d = b.pendingProps;
      wg(b);
      switch (b.tag) {
        case 2:
        case 16:
        case 15:
        case 0:
        case 11:
        case 7:
        case 8:
        case 12:
        case 9:
        case 14:
          return S(b), null;
        case 1:
          return Zf(b.type) && $f(), S(b), null;
        case 3:
          d = b.stateNode;
          zh();
          E(Wf);
          E(H);
          Eh();
          d.pendingContext && (d.context = d.pendingContext, d.pendingContext = null);
          if (null === a || null === a.child) Gg(b) ? b.flags |= 4 : null === a || a.memoizedState.isDehydrated && 0 === (b.flags & 256) || (b.flags |= 1024, null !== zg && (Fj(zg), zg = null));
          Aj(a, b);
          S(b);
          return null;
        case 5:
          Bh(b);
          var e = xh(wh.current);
          c = b.type;
          if (null !== a && null != b.stateNode) Bj(a, b, c, d, e), a.ref !== b.ref && (b.flags |= 512, b.flags |= 2097152);
          else {
            if (!d) {
              if (null === b.stateNode) throw Error(p(166));
              S(b);
              return null;
            }
            a = xh(uh.current);
            if (Gg(b)) {
              d = b.stateNode;
              c = b.type;
              var f = b.memoizedProps;
              d[Of] = b;
              d[Pf] = f;
              a = 0 !== (b.mode & 1);
              switch (c) {
                case "dialog":
                  D("cancel", d);
                  D("close", d);
                  break;
                case "iframe":
                case "object":
                case "embed":
                  D("load", d);
                  break;
                case "video":
                case "audio":
                  for (e = 0; e < lf.length; e++) D(lf[e], d);
                  break;
                case "source":
                  D("error", d);
                  break;
                case "img":
                case "image":
                case "link":
                  D(
                    "error",
                    d
                  );
                  D("load", d);
                  break;
                case "details":
                  D("toggle", d);
                  break;
                case "input":
                  Za(d, f);
                  D("invalid", d);
                  break;
                case "select":
                  d._wrapperState = { wasMultiple: !!f.multiple };
                  D("invalid", d);
                  break;
                case "textarea":
                  hb(d, f), D("invalid", d);
              }
              ub(c, f);
              e = null;
              for (var g in f) if (f.hasOwnProperty(g)) {
                var h = f[g];
                "children" === g ? "string" === typeof h ? d.textContent !== h && (true !== f.suppressHydrationWarning && Af(d.textContent, h, a), e = ["children", h]) : "number" === typeof h && d.textContent !== "" + h && (true !== f.suppressHydrationWarning && Af(
                  d.textContent,
                  h,
                  a
                ), e = ["children", "" + h]) : ea.hasOwnProperty(g) && null != h && "onScroll" === g && D("scroll", d);
              }
              switch (c) {
                case "input":
                  Va(d);
                  db(d, f, true);
                  break;
                case "textarea":
                  Va(d);
                  jb(d);
                  break;
                case "select":
                case "option":
                  break;
                default:
                  "function" === typeof f.onClick && (d.onclick = Bf);
              }
              d = e;
              b.updateQueue = d;
              null !== d && (b.flags |= 4);
            } else {
              g = 9 === e.nodeType ? e : e.ownerDocument;
              "http://www.w3.org/1999/xhtml" === a && (a = kb(c));
              "http://www.w3.org/1999/xhtml" === a ? "script" === c ? (a = g.createElement("div"), a.innerHTML = "<script><\/script>", a = a.removeChild(a.firstChild)) : "string" === typeof d.is ? a = g.createElement(c, { is: d.is }) : (a = g.createElement(c), "select" === c && (g = a, d.multiple ? g.multiple = true : d.size && (g.size = d.size))) : a = g.createElementNS(a, c);
              a[Of] = b;
              a[Pf] = d;
              zj(a, b, false, false);
              b.stateNode = a;
              a: {
                g = vb(c, d);
                switch (c) {
                  case "dialog":
                    D("cancel", a);
                    D("close", a);
                    e = d;
                    break;
                  case "iframe":
                  case "object":
                  case "embed":
                    D("load", a);
                    e = d;
                    break;
                  case "video":
                  case "audio":
                    for (e = 0; e < lf.length; e++) D(lf[e], a);
                    e = d;
                    break;
                  case "source":
                    D("error", a);
                    e = d;
                    break;
                  case "img":
                  case "image":
                  case "link":
                    D(
                      "error",
                      a
                    );
                    D("load", a);
                    e = d;
                    break;
                  case "details":
                    D("toggle", a);
                    e = d;
                    break;
                  case "input":
                    Za(a, d);
                    e = Ya(a, d);
                    D("invalid", a);
                    break;
                  case "option":
                    e = d;
                    break;
                  case "select":
                    a._wrapperState = { wasMultiple: !!d.multiple };
                    e = A({}, d, { value: void 0 });
                    D("invalid", a);
                    break;
                  case "textarea":
                    hb(a, d);
                    e = gb(a, d);
                    D("invalid", a);
                    break;
                  default:
                    e = d;
                }
                ub(c, e);
                h = e;
                for (f in h) if (h.hasOwnProperty(f)) {
                  var k = h[f];
                  "style" === f ? sb(a, k) : "dangerouslySetInnerHTML" === f ? (k = k ? k.__html : void 0, null != k && nb(a, k)) : "children" === f ? "string" === typeof k ? ("textarea" !== c || "" !== k) && ob(a, k) : "number" === typeof k && ob(a, "" + k) : "suppressContentEditableWarning" !== f && "suppressHydrationWarning" !== f && "autoFocus" !== f && (ea.hasOwnProperty(f) ? null != k && "onScroll" === f && D("scroll", a) : null != k && ta(a, f, k, g));
                }
                switch (c) {
                  case "input":
                    Va(a);
                    db(a, d, false);
                    break;
                  case "textarea":
                    Va(a);
                    jb(a);
                    break;
                  case "option":
                    null != d.value && a.setAttribute("value", "" + Sa(d.value));
                    break;
                  case "select":
                    a.multiple = !!d.multiple;
                    f = d.value;
                    null != f ? fb(a, !!d.multiple, f, false) : null != d.defaultValue && fb(
                      a,
                      !!d.multiple,
                      d.defaultValue,
                      true
                    );
                    break;
                  default:
                    "function" === typeof e.onClick && (a.onclick = Bf);
                }
                switch (c) {
                  case "button":
                  case "input":
                  case "select":
                  case "textarea":
                    d = !!d.autoFocus;
                    break a;
                  case "img":
                    d = true;
                    break a;
                  default:
                    d = false;
                }
              }
              d && (b.flags |= 4);
            }
            null !== b.ref && (b.flags |= 512, b.flags |= 2097152);
          }
          S(b);
          return null;
        case 6:
          if (a && null != b.stateNode) Cj(a, b, a.memoizedProps, d);
          else {
            if ("string" !== typeof d && null === b.stateNode) throw Error(p(166));
            c = xh(wh.current);
            xh(uh.current);
            if (Gg(b)) {
              d = b.stateNode;
              c = b.memoizedProps;
              d[Of] = b;
              if (f = d.nodeValue !== c) {
                if (a = xg, null !== a) switch (a.tag) {
                  case 3:
                    Af(d.nodeValue, c, 0 !== (a.mode & 1));
                    break;
                  case 5:
                    true !== a.memoizedProps.suppressHydrationWarning && Af(d.nodeValue, c, 0 !== (a.mode & 1));
                }
              }
              f && (b.flags |= 4);
            } else d = (9 === c.nodeType ? c : c.ownerDocument).createTextNode(d), d[Of] = b, b.stateNode = d;
          }
          S(b);
          return null;
        case 13:
          E(L);
          d = b.memoizedState;
          if (null === a || null !== a.memoizedState && null !== a.memoizedState.dehydrated) {
            if (I && null !== yg && 0 !== (b.mode & 1) && 0 === (b.flags & 128)) Hg(), Ig(), b.flags |= 98560, f = false;
            else if (f = Gg(b), null !== d && null !== d.dehydrated) {
              if (null === a) {
                if (!f) throw Error(p(318));
                f = b.memoizedState;
                f = null !== f ? f.dehydrated : null;
                if (!f) throw Error(p(317));
                f[Of] = b;
              } else Ig(), 0 === (b.flags & 128) && (b.memoizedState = null), b.flags |= 4;
              S(b);
              f = false;
            } else null !== zg && (Fj(zg), zg = null), f = true;
            if (!f) return b.flags & 65536 ? b : null;
          }
          if (0 !== (b.flags & 128)) return b.lanes = c, b;
          d = null !== d;
          d !== (null !== a && null !== a.memoizedState) && d && (b.child.flags |= 8192, 0 !== (b.mode & 1) && (null === a || 0 !== (L.current & 1) ? 0 === T && (T = 3) : tj()));
          null !== b.updateQueue && (b.flags |= 4);
          S(b);
          return null;
        case 4:
          return zh(), Aj(a, b), null === a && sf(b.stateNode.containerInfo), S(b), null;
        case 10:
          return ah(b.type._context), S(b), null;
        case 17:
          return Zf(b.type) && $f(), S(b), null;
        case 19:
          E(L);
          f = b.memoizedState;
          if (null === f) return S(b), null;
          d = 0 !== (b.flags & 128);
          g = f.rendering;
          if (null === g) if (d) Dj(f, false);
          else {
            if (0 !== T || null !== a && 0 !== (a.flags & 128)) for (a = b.child; null !== a; ) {
              g = Ch(a);
              if (null !== g) {
                b.flags |= 128;
                Dj(f, false);
                d = g.updateQueue;
                null !== d && (b.updateQueue = d, b.flags |= 4);
                b.subtreeFlags = 0;
                d = c;
                for (c = b.child; null !== c; ) f = c, a = d, f.flags &= 14680066, g = f.alternate, null === g ? (f.childLanes = 0, f.lanes = a, f.child = null, f.subtreeFlags = 0, f.memoizedProps = null, f.memoizedState = null, f.updateQueue = null, f.dependencies = null, f.stateNode = null) : (f.childLanes = g.childLanes, f.lanes = g.lanes, f.child = g.child, f.subtreeFlags = 0, f.deletions = null, f.memoizedProps = g.memoizedProps, f.memoizedState = g.memoizedState, f.updateQueue = g.updateQueue, f.type = g.type, a = g.dependencies, f.dependencies = null === a ? null : { lanes: a.lanes, firstContext: a.firstContext }), c = c.sibling;
                G(L, L.current & 1 | 2);
                return b.child;
              }
              a = a.sibling;
            }
            null !== f.tail && B() > Gj && (b.flags |= 128, d = true, Dj(f, false), b.lanes = 4194304);
          }
          else {
            if (!d) if (a = Ch(g), null !== a) {
              if (b.flags |= 128, d = true, c = a.updateQueue, null !== c && (b.updateQueue = c, b.flags |= 4), Dj(f, true), null === f.tail && "hidden" === f.tailMode && !g.alternate && !I) return S(b), null;
            } else 2 * B() - f.renderingStartTime > Gj && 1073741824 !== c && (b.flags |= 128, d = true, Dj(f, false), b.lanes = 4194304);
            f.isBackwards ? (g.sibling = b.child, b.child = g) : (c = f.last, null !== c ? c.sibling = g : b.child = g, f.last = g);
          }
          if (null !== f.tail) return b = f.tail, f.rendering = b, f.tail = b.sibling, f.renderingStartTime = B(), b.sibling = null, c = L.current, G(L, d ? c & 1 | 2 : c & 1), b;
          S(b);
          return null;
        case 22:
        case 23:
          return Hj(), d = null !== b.memoizedState, null !== a && null !== a.memoizedState !== d && (b.flags |= 8192), d && 0 !== (b.mode & 1) ? 0 !== (fj & 1073741824) && (S(b), b.subtreeFlags & 6 && (b.flags |= 8192)) : S(b), null;
        case 24:
          return null;
        case 25:
          return null;
      }
      throw Error(p(156, b.tag));
    }
    function Ij(a, b) {
      wg(b);
      switch (b.tag) {
        case 1:
          return Zf(b.type) && $f(), a = b.flags, a & 65536 ? (b.flags = a & -65537 | 128, b) : null;
        case 3:
          return zh(), E(Wf), E(H), Eh(), a = b.flags, 0 !== (a & 65536) && 0 === (a & 128) ? (b.flags = a & -65537 | 128, b) : null;
        case 5:
          return Bh(b), null;
        case 13:
          E(L);
          a = b.memoizedState;
          if (null !== a && null !== a.dehydrated) {
            if (null === b.alternate) throw Error(p(340));
            Ig();
          }
          a = b.flags;
          return a & 65536 ? (b.flags = a & -65537 | 128, b) : null;
        case 19:
          return E(L), null;
        case 4:
          return zh(), null;
        case 10:
          return ah(b.type._context), null;
        case 22:
        case 23:
          return Hj(), null;
        case 24:
          return null;
        default:
          return null;
      }
    }
    var Jj = false, U = false, Kj = "function" === typeof WeakSet ? WeakSet : Set, V = null;
    function Lj(a, b) {
      var c = a.ref;
      if (null !== c) if ("function" === typeof c) try {
        c(null);
      } catch (d) {
        W(a, b, d);
      }
      else c.current = null;
    }
    function Mj(a, b, c) {
      try {
        c();
      } catch (d) {
        W(a, b, d);
      }
    }
    var Nj = false;
    function Oj(a, b) {
      Cf = dd;
      a = Me();
      if (Ne(a)) {
        if ("selectionStart" in a) var c = { start: a.selectionStart, end: a.selectionEnd };
        else a: {
          c = (c = a.ownerDocument) && c.defaultView || window;
          var d = c.getSelection && c.getSelection();
          if (d && 0 !== d.rangeCount) {
            c = d.anchorNode;
            var e = d.anchorOffset, f = d.focusNode;
            d = d.focusOffset;
            try {
              c.nodeType, f.nodeType;
            } catch (F) {
              c = null;
              break a;
            }
            var g = 0, h = -1, k = -1, l = 0, m = 0, q = a, r = null;
            b: for (; ; ) {
              for (var y; ; ) {
                q !== c || 0 !== e && 3 !== q.nodeType || (h = g + e);
                q !== f || 0 !== d && 3 !== q.nodeType || (k = g + d);
                3 === q.nodeType && (g += q.nodeValue.length);
                if (null === (y = q.firstChild)) break;
                r = q;
                q = y;
              }
              for (; ; ) {
                if (q === a) break b;
                r === c && ++l === e && (h = g);
                r === f && ++m === d && (k = g);
                if (null !== (y = q.nextSibling)) break;
                q = r;
                r = q.parentNode;
              }
              q = y;
            }
            c = -1 === h || -1 === k ? null : { start: h, end: k };
          } else c = null;
        }
        c = c || { start: 0, end: 0 };
      } else c = null;
      Df = { focusedElem: a, selectionRange: c };
      dd = false;
      for (V = b; null !== V; ) if (b = V, a = b.child, 0 !== (b.subtreeFlags & 1028) && null !== a) a.return = b, V = a;
      else for (; null !== V; ) {
        b = V;
        try {
          var n = b.alternate;
          if (0 !== (b.flags & 1024)) switch (b.tag) {
            case 0:
            case 11:
            case 15:
              break;
            case 1:
              if (null !== n) {
                var t = n.memoizedProps, J = n.memoizedState, x = b.stateNode, w = x.getSnapshotBeforeUpdate(b.elementType === b.type ? t : Ci(b.type, t), J);
                x.__reactInternalSnapshotBeforeUpdate = w;
              }
              break;
            case 3:
              var u = b.stateNode.containerInfo;
              1 === u.nodeType ? u.textContent = "" : 9 === u.nodeType && u.documentElement && u.removeChild(u.documentElement);
              break;
            case 5:
            case 6:
            case 4:
            case 17:
              break;
            default:
              throw Error(p(163));
          }
        } catch (F) {
          W(b, b.return, F);
        }
        a = b.sibling;
        if (null !== a) {
          a.return = b.return;
          V = a;
          break;
        }
        V = b.return;
      }
      n = Nj;
      Nj = false;
      return n;
    }
    function Pj(a, b, c) {
      var d = b.updateQueue;
      d = null !== d ? d.lastEffect : null;
      if (null !== d) {
        var e = d = d.next;
        do {
          if ((e.tag & a) === a) {
            var f = e.destroy;
            e.destroy = void 0;
            void 0 !== f && Mj(b, c, f);
          }
          e = e.next;
        } while (e !== d);
      }
    }
    function Qj(a, b) {
      b = b.updateQueue;
      b = null !== b ? b.lastEffect : null;
      if (null !== b) {
        var c = b = b.next;
        do {
          if ((c.tag & a) === a) {
            var d = c.create;
            c.destroy = d();
          }
          c = c.next;
        } while (c !== b);
      }
    }
    function Rj(a) {
      var b = a.ref;
      if (null !== b) {
        var c = a.stateNode;
        switch (a.tag) {
          case 5:
            a = c;
            break;
          default:
            a = c;
        }
        "function" === typeof b ? b(a) : b.current = a;
      }
    }
    function Sj(a) {
      var b = a.alternate;
      null !== b && (a.alternate = null, Sj(b));
      a.child = null;
      a.deletions = null;
      a.sibling = null;
      5 === a.tag && (b = a.stateNode, null !== b && (delete b[Of], delete b[Pf], delete b[of], delete b[Qf], delete b[Rf]));
      a.stateNode = null;
      a.return = null;
      a.dependencies = null;
      a.memoizedProps = null;
      a.memoizedState = null;
      a.pendingProps = null;
      a.stateNode = null;
      a.updateQueue = null;
    }
    function Tj(a) {
      return 5 === a.tag || 3 === a.tag || 4 === a.tag;
    }
    function Uj(a) {
      a: for (; ; ) {
        for (; null === a.sibling; ) {
          if (null === a.return || Tj(a.return)) return null;
          a = a.return;
        }
        a.sibling.return = a.return;
        for (a = a.sibling; 5 !== a.tag && 6 !== a.tag && 18 !== a.tag; ) {
          if (a.flags & 2) continue a;
          if (null === a.child || 4 === a.tag) continue a;
          else a.child.return = a, a = a.child;
        }
        if (!(a.flags & 2)) return a.stateNode;
      }
    }
    function Vj(a, b, c) {
      var d = a.tag;
      if (5 === d || 6 === d) a = a.stateNode, b ? 8 === c.nodeType ? c.parentNode.insertBefore(a, b) : c.insertBefore(a, b) : (8 === c.nodeType ? (b = c.parentNode, b.insertBefore(a, c)) : (b = c, b.appendChild(a)), c = c._reactRootContainer, null !== c && void 0 !== c || null !== b.onclick || (b.onclick = Bf));
      else if (4 !== d && (a = a.child, null !== a)) for (Vj(a, b, c), a = a.sibling; null !== a; ) Vj(a, b, c), a = a.sibling;
    }
    function Wj(a, b, c) {
      var d = a.tag;
      if (5 === d || 6 === d) a = a.stateNode, b ? c.insertBefore(a, b) : c.appendChild(a);
      else if (4 !== d && (a = a.child, null !== a)) for (Wj(a, b, c), a = a.sibling; null !== a; ) Wj(a, b, c), a = a.sibling;
    }
    var X = null, Xj = false;
    function Yj(a, b, c) {
      for (c = c.child; null !== c; ) Zj(a, b, c), c = c.sibling;
    }
    function Zj(a, b, c) {
      if (lc && "function" === typeof lc.onCommitFiberUnmount) try {
        lc.onCommitFiberUnmount(kc, c);
      } catch (h) {
      }
      switch (c.tag) {
        case 5:
          U || Lj(c, b);
        case 6:
          var d = X, e = Xj;
          X = null;
          Yj(a, b, c);
          X = d;
          Xj = e;
          null !== X && (Xj ? (a = X, c = c.stateNode, 8 === a.nodeType ? a.parentNode.removeChild(c) : a.removeChild(c)) : X.removeChild(c.stateNode));
          break;
        case 18:
          null !== X && (Xj ? (a = X, c = c.stateNode, 8 === a.nodeType ? Kf(a.parentNode, c) : 1 === a.nodeType && Kf(a, c), bd(a)) : Kf(X, c.stateNode));
          break;
        case 4:
          d = X;
          e = Xj;
          X = c.stateNode.containerInfo;
          Xj = true;
          Yj(a, b, c);
          X = d;
          Xj = e;
          break;
        case 0:
        case 11:
        case 14:
        case 15:
          if (!U && (d = c.updateQueue, null !== d && (d = d.lastEffect, null !== d))) {
            e = d = d.next;
            do {
              var f = e, g = f.destroy;
              f = f.tag;
              void 0 !== g && (0 !== (f & 2) ? Mj(c, b, g) : 0 !== (f & 4) && Mj(c, b, g));
              e = e.next;
            } while (e !== d);
          }
          Yj(a, b, c);
          break;
        case 1:
          if (!U && (Lj(c, b), d = c.stateNode, "function" === typeof d.componentWillUnmount)) try {
            d.props = c.memoizedProps, d.state = c.memoizedState, d.componentWillUnmount();
          } catch (h) {
            W(c, b, h);
          }
          Yj(a, b, c);
          break;
        case 21:
          Yj(a, b, c);
          break;
        case 22:
          c.mode & 1 ? (U = (d = U) || null !== c.memoizedState, Yj(a, b, c), U = d) : Yj(a, b, c);
          break;
        default:
          Yj(a, b, c);
      }
    }
    function ak(a) {
      var b = a.updateQueue;
      if (null !== b) {
        a.updateQueue = null;
        var c = a.stateNode;
        null === c && (c = a.stateNode = new Kj());
        b.forEach(function(b2) {
          var d = bk.bind(null, a, b2);
          c.has(b2) || (c.add(b2), b2.then(d, d));
        });
      }
    }
    function ck(a, b) {
      var c = b.deletions;
      if (null !== c) for (var d = 0; d < c.length; d++) {
        var e = c[d];
        try {
          var f = a, g = b, h = g;
          a: for (; null !== h; ) {
            switch (h.tag) {
              case 5:
                X = h.stateNode;
                Xj = false;
                break a;
              case 3:
                X = h.stateNode.containerInfo;
                Xj = true;
                break a;
              case 4:
                X = h.stateNode.containerInfo;
                Xj = true;
                break a;
            }
            h = h.return;
          }
          if (null === X) throw Error(p(160));
          Zj(f, g, e);
          X = null;
          Xj = false;
          var k = e.alternate;
          null !== k && (k.return = null);
          e.return = null;
        } catch (l) {
          W(e, b, l);
        }
      }
      if (b.subtreeFlags & 12854) for (b = b.child; null !== b; ) dk(b, a), b = b.sibling;
    }
    function dk(a, b) {
      var c = a.alternate, d = a.flags;
      switch (a.tag) {
        case 0:
        case 11:
        case 14:
        case 15:
          ck(b, a);
          ek(a);
          if (d & 4) {
            try {
              Pj(3, a, a.return), Qj(3, a);
            } catch (t) {
              W(a, a.return, t);
            }
            try {
              Pj(5, a, a.return);
            } catch (t) {
              W(a, a.return, t);
            }
          }
          break;
        case 1:
          ck(b, a);
          ek(a);
          d & 512 && null !== c && Lj(c, c.return);
          break;
        case 5:
          ck(b, a);
          ek(a);
          d & 512 && null !== c && Lj(c, c.return);
          if (a.flags & 32) {
            var e = a.stateNode;
            try {
              ob(e, "");
            } catch (t) {
              W(a, a.return, t);
            }
          }
          if (d & 4 && (e = a.stateNode, null != e)) {
            var f = a.memoizedProps, g = null !== c ? c.memoizedProps : f, h = a.type, k = a.updateQueue;
            a.updateQueue = null;
            if (null !== k) try {
              "input" === h && "radio" === f.type && null != f.name && ab(e, f);
              vb(h, g);
              var l = vb(h, f);
              for (g = 0; g < k.length; g += 2) {
                var m = k[g], q = k[g + 1];
                "style" === m ? sb(e, q) : "dangerouslySetInnerHTML" === m ? nb(e, q) : "children" === m ? ob(e, q) : ta(e, m, q, l);
              }
              switch (h) {
                case "input":
                  bb(e, f);
                  break;
                case "textarea":
                  ib(e, f);
                  break;
                case "select":
                  var r = e._wrapperState.wasMultiple;
                  e._wrapperState.wasMultiple = !!f.multiple;
                  var y = f.value;
                  null != y ? fb(e, !!f.multiple, y, false) : r !== !!f.multiple && (null != f.defaultValue ? fb(
                    e,
                    !!f.multiple,
                    f.defaultValue,
                    true
                  ) : fb(e, !!f.multiple, f.multiple ? [] : "", false));
              }
              e[Pf] = f;
            } catch (t) {
              W(a, a.return, t);
            }
          }
          break;
        case 6:
          ck(b, a);
          ek(a);
          if (d & 4) {
            if (null === a.stateNode) throw Error(p(162));
            e = a.stateNode;
            f = a.memoizedProps;
            try {
              e.nodeValue = f;
            } catch (t) {
              W(a, a.return, t);
            }
          }
          break;
        case 3:
          ck(b, a);
          ek(a);
          if (d & 4 && null !== c && c.memoizedState.isDehydrated) try {
            bd(b.containerInfo);
          } catch (t) {
            W(a, a.return, t);
          }
          break;
        case 4:
          ck(b, a);
          ek(a);
          break;
        case 13:
          ck(b, a);
          ek(a);
          e = a.child;
          e.flags & 8192 && (f = null !== e.memoizedState, e.stateNode.isHidden = f, !f || null !== e.alternate && null !== e.alternate.memoizedState || (fk = B()));
          d & 4 && ak(a);
          break;
        case 22:
          m = null !== c && null !== c.memoizedState;
          a.mode & 1 ? (U = (l = U) || m, ck(b, a), U = l) : ck(b, a);
          ek(a);
          if (d & 8192) {
            l = null !== a.memoizedState;
            if ((a.stateNode.isHidden = l) && !m && 0 !== (a.mode & 1)) for (V = a, m = a.child; null !== m; ) {
              for (q = V = m; null !== V; ) {
                r = V;
                y = r.child;
                switch (r.tag) {
                  case 0:
                  case 11:
                  case 14:
                  case 15:
                    Pj(4, r, r.return);
                    break;
                  case 1:
                    Lj(r, r.return);
                    var n = r.stateNode;
                    if ("function" === typeof n.componentWillUnmount) {
                      d = r;
                      c = r.return;
                      try {
                        b = d, n.props = b.memoizedProps, n.state = b.memoizedState, n.componentWillUnmount();
                      } catch (t) {
                        W(d, c, t);
                      }
                    }
                    break;
                  case 5:
                    Lj(r, r.return);
                    break;
                  case 22:
                    if (null !== r.memoizedState) {
                      gk(q);
                      continue;
                    }
                }
                null !== y ? (y.return = r, V = y) : gk(q);
              }
              m = m.sibling;
            }
            a: for (m = null, q = a; ; ) {
              if (5 === q.tag) {
                if (null === m) {
                  m = q;
                  try {
                    e = q.stateNode, l ? (f = e.style, "function" === typeof f.setProperty ? f.setProperty("display", "none", "important") : f.display = "none") : (h = q.stateNode, k = q.memoizedProps.style, g = void 0 !== k && null !== k && k.hasOwnProperty("display") ? k.display : null, h.style.display = rb("display", g));
                  } catch (t) {
                    W(a, a.return, t);
                  }
                }
              } else if (6 === q.tag) {
                if (null === m) try {
                  q.stateNode.nodeValue = l ? "" : q.memoizedProps;
                } catch (t) {
                  W(a, a.return, t);
                }
              } else if ((22 !== q.tag && 23 !== q.tag || null === q.memoizedState || q === a) && null !== q.child) {
                q.child.return = q;
                q = q.child;
                continue;
              }
              if (q === a) break a;
              for (; null === q.sibling; ) {
                if (null === q.return || q.return === a) break a;
                m === q && (m = null);
                q = q.return;
              }
              m === q && (m = null);
              q.sibling.return = q.return;
              q = q.sibling;
            }
          }
          break;
        case 19:
          ck(b, a);
          ek(a);
          d & 4 && ak(a);
          break;
        case 21:
          break;
        default:
          ck(
            b,
            a
          ), ek(a);
      }
    }
    function ek(a) {
      var b = a.flags;
      if (b & 2) {
        try {
          a: {
            for (var c = a.return; null !== c; ) {
              if (Tj(c)) {
                var d = c;
                break a;
              }
              c = c.return;
            }
            throw Error(p(160));
          }
          switch (d.tag) {
            case 5:
              var e = d.stateNode;
              d.flags & 32 && (ob(e, ""), d.flags &= -33);
              var f = Uj(a);
              Wj(a, f, e);
              break;
            case 3:
            case 4:
              var g = d.stateNode.containerInfo, h = Uj(a);
              Vj(a, h, g);
              break;
            default:
              throw Error(p(161));
          }
        } catch (k) {
          W(a, a.return, k);
        }
        a.flags &= -3;
      }
      b & 4096 && (a.flags &= -4097);
    }
    function hk(a, b, c) {
      V = a;
      ik(a);
    }
    function ik(a, b, c) {
      for (var d = 0 !== (a.mode & 1); null !== V; ) {
        var e = V, f = e.child;
        if (22 === e.tag && d) {
          var g = null !== e.memoizedState || Jj;
          if (!g) {
            var h = e.alternate, k = null !== h && null !== h.memoizedState || U;
            h = Jj;
            var l = U;
            Jj = g;
            if ((U = k) && !l) for (V = e; null !== V; ) g = V, k = g.child, 22 === g.tag && null !== g.memoizedState ? jk(e) : null !== k ? (k.return = g, V = k) : jk(e);
            for (; null !== f; ) V = f, ik(f), f = f.sibling;
            V = e;
            Jj = h;
            U = l;
          }
          kk(a);
        } else 0 !== (e.subtreeFlags & 8772) && null !== f ? (f.return = e, V = f) : kk(a);
      }
    }
    function kk(a) {
      for (; null !== V; ) {
        var b = V;
        if (0 !== (b.flags & 8772)) {
          var c = b.alternate;
          try {
            if (0 !== (b.flags & 8772)) switch (b.tag) {
              case 0:
              case 11:
              case 15:
                U || Qj(5, b);
                break;
              case 1:
                var d = b.stateNode;
                if (b.flags & 4 && !U) if (null === c) d.componentDidMount();
                else {
                  var e = b.elementType === b.type ? c.memoizedProps : Ci(b.type, c.memoizedProps);
                  d.componentDidUpdate(e, c.memoizedState, d.__reactInternalSnapshotBeforeUpdate);
                }
                var f = b.updateQueue;
                null !== f && sh(b, f, d);
                break;
              case 3:
                var g = b.updateQueue;
                if (null !== g) {
                  c = null;
                  if (null !== b.child) switch (b.child.tag) {
                    case 5:
                      c = b.child.stateNode;
                      break;
                    case 1:
                      c = b.child.stateNode;
                  }
                  sh(b, g, c);
                }
                break;
              case 5:
                var h = b.stateNode;
                if (null === c && b.flags & 4) {
                  c = h;
                  var k = b.memoizedProps;
                  switch (b.type) {
                    case "button":
                    case "input":
                    case "select":
                    case "textarea":
                      k.autoFocus && c.focus();
                      break;
                    case "img":
                      k.src && (c.src = k.src);
                  }
                }
                break;
              case 6:
                break;
              case 4:
                break;
              case 12:
                break;
              case 13:
                if (null === b.memoizedState) {
                  var l = b.alternate;
                  if (null !== l) {
                    var m = l.memoizedState;
                    if (null !== m) {
                      var q = m.dehydrated;
                      null !== q && bd(q);
                    }
                  }
                }
                break;
              case 19:
              case 17:
              case 21:
              case 22:
              case 23:
              case 25:
                break;
              default:
                throw Error(p(163));
            }
            U || b.flags & 512 && Rj(b);
          } catch (r) {
            W(b, b.return, r);
          }
        }
        if (b === a) {
          V = null;
          break;
        }
        c = b.sibling;
        if (null !== c) {
          c.return = b.return;
          V = c;
          break;
        }
        V = b.return;
      }
    }
    function gk(a) {
      for (; null !== V; ) {
        var b = V;
        if (b === a) {
          V = null;
          break;
        }
        var c = b.sibling;
        if (null !== c) {
          c.return = b.return;
          V = c;
          break;
        }
        V = b.return;
      }
    }
    function jk(a) {
      for (; null !== V; ) {
        var b = V;
        try {
          switch (b.tag) {
            case 0:
            case 11:
            case 15:
              var c = b.return;
              try {
                Qj(4, b);
              } catch (k) {
                W(b, c, k);
              }
              break;
            case 1:
              var d = b.stateNode;
              if ("function" === typeof d.componentDidMount) {
                var e = b.return;
                try {
                  d.componentDidMount();
                } catch (k) {
                  W(b, e, k);
                }
              }
              var f = b.return;
              try {
                Rj(b);
              } catch (k) {
                W(b, f, k);
              }
              break;
            case 5:
              var g = b.return;
              try {
                Rj(b);
              } catch (k) {
                W(b, g, k);
              }
          }
        } catch (k) {
          W(b, b.return, k);
        }
        if (b === a) {
          V = null;
          break;
        }
        var h = b.sibling;
        if (null !== h) {
          h.return = b.return;
          V = h;
          break;
        }
        V = b.return;
      }
    }
    var lk = Math.ceil, mk = ua.ReactCurrentDispatcher, nk = ua.ReactCurrentOwner, ok = ua.ReactCurrentBatchConfig, K = 0, Q = null, Y = null, Z = 0, fj = 0, ej = Uf(0), T = 0, pk = null, rh = 0, qk = 0, rk = 0, sk = null, tk = null, fk = 0, Gj = Infinity, uk = null, Oi = false, Pi = null, Ri = null, vk = false, wk = null, xk = 0, yk = 0, zk = null, Ak = -1, Bk = 0;
    function R() {
      return 0 !== (K & 6) ? B() : -1 !== Ak ? Ak : Ak = B();
    }
    function yi(a) {
      if (0 === (a.mode & 1)) return 1;
      if (0 !== (K & 2) && 0 !== Z) return Z & -Z;
      if (null !== Kg.transition) return 0 === Bk && (Bk = yc()), Bk;
      a = C;
      if (0 !== a) return a;
      a = window.event;
      a = void 0 === a ? 16 : jd(a.type);
      return a;
    }
    function gi(a, b, c, d) {
      if (50 < yk) throw yk = 0, zk = null, Error(p(185));
      Ac(a, c, d);
      if (0 === (K & 2) || a !== Q) a === Q && (0 === (K & 2) && (qk |= c), 4 === T && Ck(a, Z)), Dk(a, d), 1 === c && 0 === K && 0 === (b.mode & 1) && (Gj = B() + 500, fg && jg());
    }
    function Dk(a, b) {
      var c = a.callbackNode;
      wc(a, b);
      var d = uc(a, a === Q ? Z : 0);
      if (0 === d) null !== c && bc(c), a.callbackNode = null, a.callbackPriority = 0;
      else if (b = d & -d, a.callbackPriority !== b) {
        null != c && bc(c);
        if (1 === b) 0 === a.tag ? ig(Ek.bind(null, a)) : hg(Ek.bind(null, a)), Jf(function() {
          0 === (K & 6) && jg();
        }), c = null;
        else {
          switch (Dc(d)) {
            case 1:
              c = fc;
              break;
            case 4:
              c = gc;
              break;
            case 16:
              c = hc;
              break;
            case 536870912:
              c = jc;
              break;
            default:
              c = hc;
          }
          c = Fk(c, Gk.bind(null, a));
        }
        a.callbackPriority = b;
        a.callbackNode = c;
      }
    }
    function Gk(a, b) {
      Ak = -1;
      Bk = 0;
      if (0 !== (K & 6)) throw Error(p(327));
      var c = a.callbackNode;
      if (Hk() && a.callbackNode !== c) return null;
      var d = uc(a, a === Q ? Z : 0);
      if (0 === d) return null;
      if (0 !== (d & 30) || 0 !== (d & a.expiredLanes) || b) b = Ik(a, d);
      else {
        b = d;
        var e = K;
        K |= 2;
        var f = Jk();
        if (Q !== a || Z !== b) uk = null, Gj = B() + 500, Kk(a, b);
        do
          try {
            Lk();
            break;
          } catch (h) {
            Mk(a, h);
          }
        while (1);
        $g();
        mk.current = f;
        K = e;
        null !== Y ? b = 0 : (Q = null, Z = 0, b = T);
      }
      if (0 !== b) {
        2 === b && (e = xc(a), 0 !== e && (d = e, b = Nk(a, e)));
        if (1 === b) throw c = pk, Kk(a, 0), Ck(a, d), Dk(a, B()), c;
        if (6 === b) Ck(a, d);
        else {
          e = a.current.alternate;
          if (0 === (d & 30) && !Ok(e) && (b = Ik(a, d), 2 === b && (f = xc(a), 0 !== f && (d = f, b = Nk(a, f))), 1 === b)) throw c = pk, Kk(a, 0), Ck(a, d), Dk(a, B()), c;
          a.finishedWork = e;
          a.finishedLanes = d;
          switch (b) {
            case 0:
            case 1:
              throw Error(p(345));
            case 2:
              Pk(a, tk, uk);
              break;
            case 3:
              Ck(a, d);
              if ((d & 130023424) === d && (b = fk + 500 - B(), 10 < b)) {
                if (0 !== uc(a, 0)) break;
                e = a.suspendedLanes;
                if ((e & d) !== d) {
                  R();
                  a.pingedLanes |= a.suspendedLanes & e;
                  break;
                }
                a.timeoutHandle = Ff(Pk.bind(null, a, tk, uk), b);
                break;
              }
              Pk(a, tk, uk);
              break;
            case 4:
              Ck(a, d);
              if ((d & 4194240) === d) break;
              b = a.eventTimes;
              for (e = -1; 0 < d; ) {
                var g = 31 - oc(d);
                f = 1 << g;
                g = b[g];
                g > e && (e = g);
                d &= ~f;
              }
              d = e;
              d = B() - d;
              d = (120 > d ? 120 : 480 > d ? 480 : 1080 > d ? 1080 : 1920 > d ? 1920 : 3e3 > d ? 3e3 : 4320 > d ? 4320 : 1960 * lk(d / 1960)) - d;
              if (10 < d) {
                a.timeoutHandle = Ff(Pk.bind(null, a, tk, uk), d);
                break;
              }
              Pk(a, tk, uk);
              break;
            case 5:
              Pk(a, tk, uk);
              break;
            default:
              throw Error(p(329));
          }
        }
      }
      Dk(a, B());
      return a.callbackNode === c ? Gk.bind(null, a) : null;
    }
    function Nk(a, b) {
      var c = sk;
      a.current.memoizedState.isDehydrated && (Kk(a, b).flags |= 256);
      a = Ik(a, b);
      2 !== a && (b = tk, tk = c, null !== b && Fj(b));
      return a;
    }
    function Fj(a) {
      null === tk ? tk = a : tk.push.apply(tk, a);
    }
    function Ok(a) {
      for (var b = a; ; ) {
        if (b.flags & 16384) {
          var c = b.updateQueue;
          if (null !== c && (c = c.stores, null !== c)) for (var d = 0; d < c.length; d++) {
            var e = c[d], f = e.getSnapshot;
            e = e.value;
            try {
              if (!He(f(), e)) return false;
            } catch (g) {
              return false;
            }
          }
        }
        c = b.child;
        if (b.subtreeFlags & 16384 && null !== c) c.return = b, b = c;
        else {
          if (b === a) break;
          for (; null === b.sibling; ) {
            if (null === b.return || b.return === a) return true;
            b = b.return;
          }
          b.sibling.return = b.return;
          b = b.sibling;
        }
      }
      return true;
    }
    function Ck(a, b) {
      b &= ~rk;
      b &= ~qk;
      a.suspendedLanes |= b;
      a.pingedLanes &= ~b;
      for (a = a.expirationTimes; 0 < b; ) {
        var c = 31 - oc(b), d = 1 << c;
        a[c] = -1;
        b &= ~d;
      }
    }
    function Ek(a) {
      if (0 !== (K & 6)) throw Error(p(327));
      Hk();
      var b = uc(a, 0);
      if (0 === (b & 1)) return Dk(a, B()), null;
      var c = Ik(a, b);
      if (0 !== a.tag && 2 === c) {
        var d = xc(a);
        0 !== d && (b = d, c = Nk(a, d));
      }
      if (1 === c) throw c = pk, Kk(a, 0), Ck(a, b), Dk(a, B()), c;
      if (6 === c) throw Error(p(345));
      a.finishedWork = a.current.alternate;
      a.finishedLanes = b;
      Pk(a, tk, uk);
      Dk(a, B());
      return null;
    }
    function Qk(a, b) {
      var c = K;
      K |= 1;
      try {
        return a(b);
      } finally {
        K = c, 0 === K && (Gj = B() + 500, fg && jg());
      }
    }
    function Rk(a) {
      null !== wk && 0 === wk.tag && 0 === (K & 6) && Hk();
      var b = K;
      K |= 1;
      var c = ok.transition, d = C;
      try {
        if (ok.transition = null, C = 1, a) return a();
      } finally {
        C = d, ok.transition = c, K = b, 0 === (K & 6) && jg();
      }
    }
    function Hj() {
      fj = ej.current;
      E(ej);
    }
    function Kk(a, b) {
      a.finishedWork = null;
      a.finishedLanes = 0;
      var c = a.timeoutHandle;
      -1 !== c && (a.timeoutHandle = -1, Gf(c));
      if (null !== Y) for (c = Y.return; null !== c; ) {
        var d = c;
        wg(d);
        switch (d.tag) {
          case 1:
            d = d.type.childContextTypes;
            null !== d && void 0 !== d && $f();
            break;
          case 3:
            zh();
            E(Wf);
            E(H);
            Eh();
            break;
          case 5:
            Bh(d);
            break;
          case 4:
            zh();
            break;
          case 13:
            E(L);
            break;
          case 19:
            E(L);
            break;
          case 10:
            ah(d.type._context);
            break;
          case 22:
          case 23:
            Hj();
        }
        c = c.return;
      }
      Q = a;
      Y = a = Pg(a.current, null);
      Z = fj = b;
      T = 0;
      pk = null;
      rk = qk = rh = 0;
      tk = sk = null;
      if (null !== fh) {
        for (b = 0; b < fh.length; b++) if (c = fh[b], d = c.interleaved, null !== d) {
          c.interleaved = null;
          var e = d.next, f = c.pending;
          if (null !== f) {
            var g = f.next;
            f.next = e;
            d.next = g;
          }
          c.pending = d;
        }
        fh = null;
      }
      return a;
    }
    function Mk(a, b) {
      do {
        var c = Y;
        try {
          $g();
          Fh.current = Rh;
          if (Ih) {
            for (var d = M.memoizedState; null !== d; ) {
              var e = d.queue;
              null !== e && (e.pending = null);
              d = d.next;
            }
            Ih = false;
          }
          Hh = 0;
          O = N = M = null;
          Jh = false;
          Kh = 0;
          nk.current = null;
          if (null === c || null === c.return) {
            T = 1;
            pk = b;
            Y = null;
            break;
          }
          a: {
            var f = a, g = c.return, h = c, k = b;
            b = Z;
            h.flags |= 32768;
            if (null !== k && "object" === typeof k && "function" === typeof k.then) {
              var l = k, m = h, q = m.tag;
              if (0 === (m.mode & 1) && (0 === q || 11 === q || 15 === q)) {
                var r = m.alternate;
                r ? (m.updateQueue = r.updateQueue, m.memoizedState = r.memoizedState, m.lanes = r.lanes) : (m.updateQueue = null, m.memoizedState = null);
              }
              var y = Ui(g);
              if (null !== y) {
                y.flags &= -257;
                Vi(y, g, h, f, b);
                y.mode & 1 && Si(f, l, b);
                b = y;
                k = l;
                var n = b.updateQueue;
                if (null === n) {
                  var t = /* @__PURE__ */ new Set();
                  t.add(k);
                  b.updateQueue = t;
                } else n.add(k);
                break a;
              } else {
                if (0 === (b & 1)) {
                  Si(f, l, b);
                  tj();
                  break a;
                }
                k = Error(p(426));
              }
            } else if (I && h.mode & 1) {
              var J = Ui(g);
              if (null !== J) {
                0 === (J.flags & 65536) && (J.flags |= 256);
                Vi(J, g, h, f, b);
                Jg(Ji(k, h));
                break a;
              }
            }
            f = k = Ji(k, h);
            4 !== T && (T = 2);
            null === sk ? sk = [f] : sk.push(f);
            f = g;
            do {
              switch (f.tag) {
                case 3:
                  f.flags |= 65536;
                  b &= -b;
                  f.lanes |= b;
                  var x = Ni(f, k, b);
                  ph(f, x);
                  break a;
                case 1:
                  h = k;
                  var w = f.type, u = f.stateNode;
                  if (0 === (f.flags & 128) && ("function" === typeof w.getDerivedStateFromError || null !== u && "function" === typeof u.componentDidCatch && (null === Ri || !Ri.has(u)))) {
                    f.flags |= 65536;
                    b &= -b;
                    f.lanes |= b;
                    var F = Qi(f, h, b);
                    ph(f, F);
                    break a;
                  }
              }
              f = f.return;
            } while (null !== f);
          }
          Sk(c);
        } catch (na) {
          b = na;
          Y === c && null !== c && (Y = c = c.return);
          continue;
        }
        break;
      } while (1);
    }
    function Jk() {
      var a = mk.current;
      mk.current = Rh;
      return null === a ? Rh : a;
    }
    function tj() {
      if (0 === T || 3 === T || 2 === T) T = 4;
      null === Q || 0 === (rh & 268435455) && 0 === (qk & 268435455) || Ck(Q, Z);
    }
    function Ik(a, b) {
      var c = K;
      K |= 2;
      var d = Jk();
      if (Q !== a || Z !== b) uk = null, Kk(a, b);
      do
        try {
          Tk();
          break;
        } catch (e) {
          Mk(a, e);
        }
      while (1);
      $g();
      K = c;
      mk.current = d;
      if (null !== Y) throw Error(p(261));
      Q = null;
      Z = 0;
      return T;
    }
    function Tk() {
      for (; null !== Y; ) Uk(Y);
    }
    function Lk() {
      for (; null !== Y && !cc(); ) Uk(Y);
    }
    function Uk(a) {
      var b = Vk(a.alternate, a, fj);
      a.memoizedProps = a.pendingProps;
      null === b ? Sk(a) : Y = b;
      nk.current = null;
    }
    function Sk(a) {
      var b = a;
      do {
        var c = b.alternate;
        a = b.return;
        if (0 === (b.flags & 32768)) {
          if (c = Ej(c, b, fj), null !== c) {
            Y = c;
            return;
          }
        } else {
          c = Ij(c, b);
          if (null !== c) {
            c.flags &= 32767;
            Y = c;
            return;
          }
          if (null !== a) a.flags |= 32768, a.subtreeFlags = 0, a.deletions = null;
          else {
            T = 6;
            Y = null;
            return;
          }
        }
        b = b.sibling;
        if (null !== b) {
          Y = b;
          return;
        }
        Y = b = a;
      } while (null !== b);
      0 === T && (T = 5);
    }
    function Pk(a, b, c) {
      var d = C, e = ok.transition;
      try {
        ok.transition = null, C = 1, Wk(a, b, c, d);
      } finally {
        ok.transition = e, C = d;
      }
      return null;
    }
    function Wk(a, b, c, d) {
      do
        Hk();
      while (null !== wk);
      if (0 !== (K & 6)) throw Error(p(327));
      c = a.finishedWork;
      var e = a.finishedLanes;
      if (null === c) return null;
      a.finishedWork = null;
      a.finishedLanes = 0;
      if (c === a.current) throw Error(p(177));
      a.callbackNode = null;
      a.callbackPriority = 0;
      var f = c.lanes | c.childLanes;
      Bc(a, f);
      a === Q && (Y = Q = null, Z = 0);
      0 === (c.subtreeFlags & 2064) && 0 === (c.flags & 2064) || vk || (vk = true, Fk(hc, function() {
        Hk();
        return null;
      }));
      f = 0 !== (c.flags & 15990);
      if (0 !== (c.subtreeFlags & 15990) || f) {
        f = ok.transition;
        ok.transition = null;
        var g = C;
        C = 1;
        var h = K;
        K |= 4;
        nk.current = null;
        Oj(a, c);
        dk(c, a);
        Oe(Df);
        dd = !!Cf;
        Df = Cf = null;
        a.current = c;
        hk(c);
        dc();
        K = h;
        C = g;
        ok.transition = f;
      } else a.current = c;
      vk && (vk = false, wk = a, xk = e);
      f = a.pendingLanes;
      0 === f && (Ri = null);
      mc(c.stateNode);
      Dk(a, B());
      if (null !== b) for (d = a.onRecoverableError, c = 0; c < b.length; c++) e = b[c], d(e.value, { componentStack: e.stack, digest: e.digest });
      if (Oi) throw Oi = false, a = Pi, Pi = null, a;
      0 !== (xk & 1) && 0 !== a.tag && Hk();
      f = a.pendingLanes;
      0 !== (f & 1) ? a === zk ? yk++ : (yk = 0, zk = a) : yk = 0;
      jg();
      return null;
    }
    function Hk() {
      if (null !== wk) {
        var a = Dc(xk), b = ok.transition, c = C;
        try {
          ok.transition = null;
          C = 16 > a ? 16 : a;
          if (null === wk) var d = false;
          else {
            a = wk;
            wk = null;
            xk = 0;
            if (0 !== (K & 6)) throw Error(p(331));
            var e = K;
            K |= 4;
            for (V = a.current; null !== V; ) {
              var f = V, g = f.child;
              if (0 !== (V.flags & 16)) {
                var h = f.deletions;
                if (null !== h) {
                  for (var k = 0; k < h.length; k++) {
                    var l = h[k];
                    for (V = l; null !== V; ) {
                      var m = V;
                      switch (m.tag) {
                        case 0:
                        case 11:
                        case 15:
                          Pj(8, m, f);
                      }
                      var q = m.child;
                      if (null !== q) q.return = m, V = q;
                      else for (; null !== V; ) {
                        m = V;
                        var r = m.sibling, y = m.return;
                        Sj(m);
                        if (m === l) {
                          V = null;
                          break;
                        }
                        if (null !== r) {
                          r.return = y;
                          V = r;
                          break;
                        }
                        V = y;
                      }
                    }
                  }
                  var n = f.alternate;
                  if (null !== n) {
                    var t = n.child;
                    if (null !== t) {
                      n.child = null;
                      do {
                        var J = t.sibling;
                        t.sibling = null;
                        t = J;
                      } while (null !== t);
                    }
                  }
                  V = f;
                }
              }
              if (0 !== (f.subtreeFlags & 2064) && null !== g) g.return = f, V = g;
              else b: for (; null !== V; ) {
                f = V;
                if (0 !== (f.flags & 2048)) switch (f.tag) {
                  case 0:
                  case 11:
                  case 15:
                    Pj(9, f, f.return);
                }
                var x = f.sibling;
                if (null !== x) {
                  x.return = f.return;
                  V = x;
                  break b;
                }
                V = f.return;
              }
            }
            var w = a.current;
            for (V = w; null !== V; ) {
              g = V;
              var u = g.child;
              if (0 !== (g.subtreeFlags & 2064) && null !== u) u.return = g, V = u;
              else b: for (g = w; null !== V; ) {
                h = V;
                if (0 !== (h.flags & 2048)) try {
                  switch (h.tag) {
                    case 0:
                    case 11:
                    case 15:
                      Qj(9, h);
                  }
                } catch (na) {
                  W(h, h.return, na);
                }
                if (h === g) {
                  V = null;
                  break b;
                }
                var F = h.sibling;
                if (null !== F) {
                  F.return = h.return;
                  V = F;
                  break b;
                }
                V = h.return;
              }
            }
            K = e;
            jg();
            if (lc && "function" === typeof lc.onPostCommitFiberRoot) try {
              lc.onPostCommitFiberRoot(kc, a);
            } catch (na) {
            }
            d = true;
          }
          return d;
        } finally {
          C = c, ok.transition = b;
        }
      }
      return false;
    }
    function Xk(a, b, c) {
      b = Ji(c, b);
      b = Ni(a, b, 1);
      a = nh(a, b, 1);
      b = R();
      null !== a && (Ac(a, 1, b), Dk(a, b));
    }
    function W(a, b, c) {
      if (3 === a.tag) Xk(a, a, c);
      else for (; null !== b; ) {
        if (3 === b.tag) {
          Xk(b, a, c);
          break;
        } else if (1 === b.tag) {
          var d = b.stateNode;
          if ("function" === typeof b.type.getDerivedStateFromError || "function" === typeof d.componentDidCatch && (null === Ri || !Ri.has(d))) {
            a = Ji(c, a);
            a = Qi(b, a, 1);
            b = nh(b, a, 1);
            a = R();
            null !== b && (Ac(b, 1, a), Dk(b, a));
            break;
          }
        }
        b = b.return;
      }
    }
    function Ti(a, b, c) {
      var d = a.pingCache;
      null !== d && d.delete(b);
      b = R();
      a.pingedLanes |= a.suspendedLanes & c;
      Q === a && (Z & c) === c && (4 === T || 3 === T && (Z & 130023424) === Z && 500 > B() - fk ? Kk(a, 0) : rk |= c);
      Dk(a, b);
    }
    function Yk(a, b) {
      0 === b && (0 === (a.mode & 1) ? b = 1 : (b = sc, sc <<= 1, 0 === (sc & 130023424) && (sc = 4194304)));
      var c = R();
      a = ih(a, b);
      null !== a && (Ac(a, b, c), Dk(a, c));
    }
    function uj(a) {
      var b = a.memoizedState, c = 0;
      null !== b && (c = b.retryLane);
      Yk(a, c);
    }
    function bk(a, b) {
      var c = 0;
      switch (a.tag) {
        case 13:
          var d = a.stateNode;
          var e = a.memoizedState;
          null !== e && (c = e.retryLane);
          break;
        case 19:
          d = a.stateNode;
          break;
        default:
          throw Error(p(314));
      }
      null !== d && d.delete(b);
      Yk(a, c);
    }
    var Vk;
    Vk = function(a, b, c) {
      if (null !== a) if (a.memoizedProps !== b.pendingProps || Wf.current) dh = true;
      else {
        if (0 === (a.lanes & c) && 0 === (b.flags & 128)) return dh = false, yj(a, b, c);
        dh = 0 !== (a.flags & 131072) ? true : false;
      }
      else dh = false, I && 0 !== (b.flags & 1048576) && ug(b, ng, b.index);
      b.lanes = 0;
      switch (b.tag) {
        case 2:
          var d = b.type;
          ij(a, b);
          a = b.pendingProps;
          var e = Yf(b, H.current);
          ch(b, c);
          e = Nh(null, b, d, a, e, c);
          var f = Sh();
          b.flags |= 1;
          "object" === typeof e && null !== e && "function" === typeof e.render && void 0 === e.$$typeof ? (b.tag = 1, b.memoizedState = null, b.updateQueue = null, Zf(d) ? (f = true, cg(b)) : f = false, b.memoizedState = null !== e.state && void 0 !== e.state ? e.state : null, kh(b), e.updater = Ei, b.stateNode = e, e._reactInternals = b, Ii(b, d, a, c), b = jj(null, b, d, true, f, c)) : (b.tag = 0, I && f && vg(b), Xi(null, b, e, c), b = b.child);
          return b;
        case 16:
          d = b.elementType;
          a: {
            ij(a, b);
            a = b.pendingProps;
            e = d._init;
            d = e(d._payload);
            b.type = d;
            e = b.tag = Zk(d);
            a = Ci(d, a);
            switch (e) {
              case 0:
                b = cj(null, b, d, a, c);
                break a;
              case 1:
                b = hj(null, b, d, a, c);
                break a;
              case 11:
                b = Yi(null, b, d, a, c);
                break a;
              case 14:
                b = $i(null, b, d, Ci(d.type, a), c);
                break a;
            }
            throw Error(p(
              306,
              d,
              ""
            ));
          }
          return b;
        case 0:
          return d = b.type, e = b.pendingProps, e = b.elementType === d ? e : Ci(d, e), cj(a, b, d, e, c);
        case 1:
          return d = b.type, e = b.pendingProps, e = b.elementType === d ? e : Ci(d, e), hj(a, b, d, e, c);
        case 3:
          a: {
            kj(b);
            if (null === a) throw Error(p(387));
            d = b.pendingProps;
            f = b.memoizedState;
            e = f.element;
            lh(a, b);
            qh(b, d, null, c);
            var g = b.memoizedState;
            d = g.element;
            if (f.isDehydrated) if (f = { element: d, isDehydrated: false, cache: g.cache, pendingSuspenseBoundaries: g.pendingSuspenseBoundaries, transitions: g.transitions }, b.updateQueue.baseState = f, b.memoizedState = f, b.flags & 256) {
              e = Ji(Error(p(423)), b);
              b = lj(a, b, d, c, e);
              break a;
            } else if (d !== e) {
              e = Ji(Error(p(424)), b);
              b = lj(a, b, d, c, e);
              break a;
            } else for (yg = Lf(b.stateNode.containerInfo.firstChild), xg = b, I = true, zg = null, c = Vg(b, null, d, c), b.child = c; c; ) c.flags = c.flags & -3 | 4096, c = c.sibling;
            else {
              Ig();
              if (d === e) {
                b = Zi(a, b, c);
                break a;
              }
              Xi(a, b, d, c);
            }
            b = b.child;
          }
          return b;
        case 5:
          return Ah(b), null === a && Eg(b), d = b.type, e = b.pendingProps, f = null !== a ? a.memoizedProps : null, g = e.children, Ef(d, e) ? g = null : null !== f && Ef(d, f) && (b.flags |= 32), gj(a, b), Xi(a, b, g, c), b.child;
        case 6:
          return null === a && Eg(b), null;
        case 13:
          return oj(a, b, c);
        case 4:
          return yh(b, b.stateNode.containerInfo), d = b.pendingProps, null === a ? b.child = Ug(b, null, d, c) : Xi(a, b, d, c), b.child;
        case 11:
          return d = b.type, e = b.pendingProps, e = b.elementType === d ? e : Ci(d, e), Yi(a, b, d, e, c);
        case 7:
          return Xi(a, b, b.pendingProps, c), b.child;
        case 8:
          return Xi(a, b, b.pendingProps.children, c), b.child;
        case 12:
          return Xi(a, b, b.pendingProps.children, c), b.child;
        case 10:
          a: {
            d = b.type._context;
            e = b.pendingProps;
            f = b.memoizedProps;
            g = e.value;
            G(Wg, d._currentValue);
            d._currentValue = g;
            if (null !== f) if (He(f.value, g)) {
              if (f.children === e.children && !Wf.current) {
                b = Zi(a, b, c);
                break a;
              }
            } else for (f = b.child, null !== f && (f.return = b); null !== f; ) {
              var h = f.dependencies;
              if (null !== h) {
                g = f.child;
                for (var k = h.firstContext; null !== k; ) {
                  if (k.context === d) {
                    if (1 === f.tag) {
                      k = mh(-1, c & -c);
                      k.tag = 2;
                      var l = f.updateQueue;
                      if (null !== l) {
                        l = l.shared;
                        var m = l.pending;
                        null === m ? k.next = k : (k.next = m.next, m.next = k);
                        l.pending = k;
                      }
                    }
                    f.lanes |= c;
                    k = f.alternate;
                    null !== k && (k.lanes |= c);
                    bh(
                      f.return,
                      c,
                      b
                    );
                    h.lanes |= c;
                    break;
                  }
                  k = k.next;
                }
              } else if (10 === f.tag) g = f.type === b.type ? null : f.child;
              else if (18 === f.tag) {
                g = f.return;
                if (null === g) throw Error(p(341));
                g.lanes |= c;
                h = g.alternate;
                null !== h && (h.lanes |= c);
                bh(g, c, b);
                g = f.sibling;
              } else g = f.child;
              if (null !== g) g.return = f;
              else for (g = f; null !== g; ) {
                if (g === b) {
                  g = null;
                  break;
                }
                f = g.sibling;
                if (null !== f) {
                  f.return = g.return;
                  g = f;
                  break;
                }
                g = g.return;
              }
              f = g;
            }
            Xi(a, b, e.children, c);
            b = b.child;
          }
          return b;
        case 9:
          return e = b.type, d = b.pendingProps.children, ch(b, c), e = eh(e), d = d(e), b.flags |= 1, Xi(a, b, d, c), b.child;
        case 14:
          return d = b.type, e = Ci(d, b.pendingProps), e = Ci(d.type, e), $i(a, b, d, e, c);
        case 15:
          return bj(a, b, b.type, b.pendingProps, c);
        case 17:
          return d = b.type, e = b.pendingProps, e = b.elementType === d ? e : Ci(d, e), ij(a, b), b.tag = 1, Zf(d) ? (a = true, cg(b)) : a = false, ch(b, c), Gi(b, d, e), Ii(b, d, e, c), jj(null, b, d, true, a, c);
        case 19:
          return xj(a, b, c);
        case 22:
          return dj(a, b, c);
      }
      throw Error(p(156, b.tag));
    };
    function Fk(a, b) {
      return ac(a, b);
    }
    function $k(a, b, c, d) {
      this.tag = a;
      this.key = c;
      this.sibling = this.child = this.return = this.stateNode = this.type = this.elementType = null;
      this.index = 0;
      this.ref = null;
      this.pendingProps = b;
      this.dependencies = this.memoizedState = this.updateQueue = this.memoizedProps = null;
      this.mode = d;
      this.subtreeFlags = this.flags = 0;
      this.deletions = null;
      this.childLanes = this.lanes = 0;
      this.alternate = null;
    }
    function Bg(a, b, c, d) {
      return new $k(a, b, c, d);
    }
    function aj(a) {
      a = a.prototype;
      return !(!a || !a.isReactComponent);
    }
    function Zk(a) {
      if ("function" === typeof a) return aj(a) ? 1 : 0;
      if (void 0 !== a && null !== a) {
        a = a.$$typeof;
        if (a === Da) return 11;
        if (a === Ga) return 14;
      }
      return 2;
    }
    function Pg(a, b) {
      var c = a.alternate;
      null === c ? (c = Bg(a.tag, b, a.key, a.mode), c.elementType = a.elementType, c.type = a.type, c.stateNode = a.stateNode, c.alternate = a, a.alternate = c) : (c.pendingProps = b, c.type = a.type, c.flags = 0, c.subtreeFlags = 0, c.deletions = null);
      c.flags = a.flags & 14680064;
      c.childLanes = a.childLanes;
      c.lanes = a.lanes;
      c.child = a.child;
      c.memoizedProps = a.memoizedProps;
      c.memoizedState = a.memoizedState;
      c.updateQueue = a.updateQueue;
      b = a.dependencies;
      c.dependencies = null === b ? null : { lanes: b.lanes, firstContext: b.firstContext };
      c.sibling = a.sibling;
      c.index = a.index;
      c.ref = a.ref;
      return c;
    }
    function Rg(a, b, c, d, e, f) {
      var g = 2;
      d = a;
      if ("function" === typeof a) aj(a) && (g = 1);
      else if ("string" === typeof a) g = 5;
      else a: switch (a) {
        case ya:
          return Tg(c.children, e, f, b);
        case za:
          g = 8;
          e |= 8;
          break;
        case Aa:
          return a = Bg(12, c, b, e | 2), a.elementType = Aa, a.lanes = f, a;
        case Ea:
          return a = Bg(13, c, b, e), a.elementType = Ea, a.lanes = f, a;
        case Fa:
          return a = Bg(19, c, b, e), a.elementType = Fa, a.lanes = f, a;
        case Ia:
          return pj(c, e, f, b);
        default:
          if ("object" === typeof a && null !== a) switch (a.$$typeof) {
            case Ba:
              g = 10;
              break a;
            case Ca:
              g = 9;
              break a;
            case Da:
              g = 11;
              break a;
            case Ga:
              g = 14;
              break a;
            case Ha:
              g = 16;
              d = null;
              break a;
          }
          throw Error(p(130, null == a ? a : typeof a, ""));
      }
      b = Bg(g, c, b, e);
      b.elementType = a;
      b.type = d;
      b.lanes = f;
      return b;
    }
    function Tg(a, b, c, d) {
      a = Bg(7, a, d, b);
      a.lanes = c;
      return a;
    }
    function pj(a, b, c, d) {
      a = Bg(22, a, d, b);
      a.elementType = Ia;
      a.lanes = c;
      a.stateNode = { isHidden: false };
      return a;
    }
    function Qg(a, b, c) {
      a = Bg(6, a, null, b);
      a.lanes = c;
      return a;
    }
    function Sg(a, b, c) {
      b = Bg(4, null !== a.children ? a.children : [], a.key, b);
      b.lanes = c;
      b.stateNode = { containerInfo: a.containerInfo, pendingChildren: null, implementation: a.implementation };
      return b;
    }
    function al(a, b, c, d, e) {
      this.tag = b;
      this.containerInfo = a;
      this.finishedWork = this.pingCache = this.current = this.pendingChildren = null;
      this.timeoutHandle = -1;
      this.callbackNode = this.pendingContext = this.context = null;
      this.callbackPriority = 0;
      this.eventTimes = zc(0);
      this.expirationTimes = zc(-1);
      this.entangledLanes = this.finishedLanes = this.mutableReadLanes = this.expiredLanes = this.pingedLanes = this.suspendedLanes = this.pendingLanes = 0;
      this.entanglements = zc(0);
      this.identifierPrefix = d;
      this.onRecoverableError = e;
      this.mutableSourceEagerHydrationData = null;
    }
    function bl(a, b, c, d, e, f, g, h, k) {
      a = new al(a, b, c, h, k);
      1 === b ? (b = 1, true === f && (b |= 8)) : b = 0;
      f = Bg(3, null, null, b);
      a.current = f;
      f.stateNode = a;
      f.memoizedState = { element: d, isDehydrated: c, cache: null, transitions: null, pendingSuspenseBoundaries: null };
      kh(f);
      return a;
    }
    function cl(a, b, c) {
      var d = 3 < arguments.length && void 0 !== arguments[3] ? arguments[3] : null;
      return { $$typeof: wa, key: null == d ? null : "" + d, children: a, containerInfo: b, implementation: c };
    }
    function dl(a) {
      if (!a) return Vf;
      a = a._reactInternals;
      a: {
        if (Vb(a) !== a || 1 !== a.tag) throw Error(p(170));
        var b = a;
        do {
          switch (b.tag) {
            case 3:
              b = b.stateNode.context;
              break a;
            case 1:
              if (Zf(b.type)) {
                b = b.stateNode.__reactInternalMemoizedMergedChildContext;
                break a;
              }
          }
          b = b.return;
        } while (null !== b);
        throw Error(p(171));
      }
      if (1 === a.tag) {
        var c = a.type;
        if (Zf(c)) return bg(a, c, b);
      }
      return b;
    }
    function el(a, b, c, d, e, f, g, h, k) {
      a = bl(c, d, true, a, e, f, g, h, k);
      a.context = dl(null);
      c = a.current;
      d = R();
      e = yi(c);
      f = mh(d, e);
      f.callback = void 0 !== b && null !== b ? b : null;
      nh(c, f, e);
      a.current.lanes = e;
      Ac(a, e, d);
      Dk(a, d);
      return a;
    }
    function fl(a, b, c, d) {
      var e = b.current, f = R(), g = yi(e);
      c = dl(c);
      null === b.context ? b.context = c : b.pendingContext = c;
      b = mh(f, g);
      b.payload = { element: a };
      d = void 0 === d ? null : d;
      null !== d && (b.callback = d);
      a = nh(e, b, g);
      null !== a && (gi(a, e, g, f), oh(a, e, g));
      return g;
    }
    function gl(a) {
      a = a.current;
      if (!a.child) return null;
      switch (a.child.tag) {
        case 5:
          return a.child.stateNode;
        default:
          return a.child.stateNode;
      }
    }
    function hl(a, b) {
      a = a.memoizedState;
      if (null !== a && null !== a.dehydrated) {
        var c = a.retryLane;
        a.retryLane = 0 !== c && c < b ? c : b;
      }
    }
    function il(a, b) {
      hl(a, b);
      (a = a.alternate) && hl(a, b);
    }
    function jl() {
      return null;
    }
    var kl = "function" === typeof reportError ? reportError : function(a) {
      console.error(a);
    };
    function ll(a) {
      this._internalRoot = a;
    }
    ml.prototype.render = ll.prototype.render = function(a) {
      var b = this._internalRoot;
      if (null === b) throw Error(p(409));
      fl(a, b, null, null);
    };
    ml.prototype.unmount = ll.prototype.unmount = function() {
      var a = this._internalRoot;
      if (null !== a) {
        this._internalRoot = null;
        var b = a.containerInfo;
        Rk(function() {
          fl(null, a, null, null);
        });
        b[uf] = null;
      }
    };
    function ml(a) {
      this._internalRoot = a;
    }
    ml.prototype.unstable_scheduleHydration = function(a) {
      if (a) {
        var b = Hc();
        a = { blockedOn: null, target: a, priority: b };
        for (var c = 0; c < Qc.length && 0 !== b && b < Qc[c].priority; c++) ;
        Qc.splice(c, 0, a);
        0 === c && Vc(a);
      }
    };
    function nl(a) {
      return !(!a || 1 !== a.nodeType && 9 !== a.nodeType && 11 !== a.nodeType);
    }
    function ol(a) {
      return !(!a || 1 !== a.nodeType && 9 !== a.nodeType && 11 !== a.nodeType && (8 !== a.nodeType || " react-mount-point-unstable " !== a.nodeValue));
    }
    function pl() {
    }
    function ql(a, b, c, d, e) {
      if (e) {
        if ("function" === typeof d) {
          var f = d;
          d = function() {
            var a2 = gl(g);
            f.call(a2);
          };
        }
        var g = el(b, d, a, 0, null, false, false, "", pl);
        a._reactRootContainer = g;
        a[uf] = g.current;
        sf(8 === a.nodeType ? a.parentNode : a);
        Rk();
        return g;
      }
      for (; e = a.lastChild; ) a.removeChild(e);
      if ("function" === typeof d) {
        var h = d;
        d = function() {
          var a2 = gl(k);
          h.call(a2);
        };
      }
      var k = bl(a, 0, false, null, null, false, false, "", pl);
      a._reactRootContainer = k;
      a[uf] = k.current;
      sf(8 === a.nodeType ? a.parentNode : a);
      Rk(function() {
        fl(b, k, c, d);
      });
      return k;
    }
    function rl(a, b, c, d, e) {
      var f = c._reactRootContainer;
      if (f) {
        var g = f;
        if ("function" === typeof e) {
          var h = e;
          e = function() {
            var a2 = gl(g);
            h.call(a2);
          };
        }
        fl(b, g, a, e);
      } else g = ql(c, b, a, e, d);
      return gl(g);
    }
    Ec = function(a) {
      switch (a.tag) {
        case 3:
          var b = a.stateNode;
          if (b.current.memoizedState.isDehydrated) {
            var c = tc(b.pendingLanes);
            0 !== c && (Cc(b, c | 1), Dk(b, B()), 0 === (K & 6) && (Gj = B() + 500, jg()));
          }
          break;
        case 13:
          Rk(function() {
            var b2 = ih(a, 1);
            if (null !== b2) {
              var c2 = R();
              gi(b2, a, 1, c2);
            }
          }), il(a, 1);
      }
    };
    Fc = function(a) {
      if (13 === a.tag) {
        var b = ih(a, 134217728);
        if (null !== b) {
          var c = R();
          gi(b, a, 134217728, c);
        }
        il(a, 134217728);
      }
    };
    Gc = function(a) {
      if (13 === a.tag) {
        var b = yi(a), c = ih(a, b);
        if (null !== c) {
          var d = R();
          gi(c, a, b, d);
        }
        il(a, b);
      }
    };
    Hc = function() {
      return C;
    };
    Ic = function(a, b) {
      var c = C;
      try {
        return C = a, b();
      } finally {
        C = c;
      }
    };
    yb = function(a, b, c) {
      switch (b) {
        case "input":
          bb(a, c);
          b = c.name;
          if ("radio" === c.type && null != b) {
            for (c = a; c.parentNode; ) c = c.parentNode;
            c = c.querySelectorAll("input[name=" + JSON.stringify("" + b) + '][type="radio"]');
            for (b = 0; b < c.length; b++) {
              var d = c[b];
              if (d !== a && d.form === a.form) {
                var e = Db(d);
                if (!e) throw Error(p(90));
                Wa(d);
                bb(d, e);
              }
            }
          }
          break;
        case "textarea":
          ib(a, c);
          break;
        case "select":
          b = c.value, null != b && fb(a, !!c.multiple, b, false);
      }
    };
    Gb = Qk;
    Hb = Rk;
    var sl = { usingClientEntryPoint: false, Events: [Cb, ue, Db, Eb, Fb, Qk] }, tl = { findFiberByHostInstance: Wc, bundleType: 0, version: "18.3.1", rendererPackageName: "react-dom" };
    var ul = { bundleType: tl.bundleType, version: tl.version, rendererPackageName: tl.rendererPackageName, rendererConfig: tl.rendererConfig, overrideHookState: null, overrideHookStateDeletePath: null, overrideHookStateRenamePath: null, overrideProps: null, overridePropsDeletePath: null, overridePropsRenamePath: null, setErrorHandler: null, setSuspenseHandler: null, scheduleUpdate: null, currentDispatcherRef: ua.ReactCurrentDispatcher, findHostInstanceByFiber: function(a) {
      a = Zb(a);
      return null === a ? null : a.stateNode;
    }, findFiberByHostInstance: tl.findFiberByHostInstance || jl, findHostInstancesForRefresh: null, scheduleRefresh: null, scheduleRoot: null, setRefreshHandler: null, getCurrentFiber: null, reconcilerVersion: "18.3.1-next-f1338f8080-20240426" };
    if ("undefined" !== typeof __REACT_DEVTOOLS_GLOBAL_HOOK__) {
      var vl = __REACT_DEVTOOLS_GLOBAL_HOOK__;
      if (!vl.isDisabled && vl.supportsFiber) try {
        kc = vl.inject(ul), lc = vl;
      } catch (a) {
      }
    }
    reactDom_production_min.__SECRET_INTERNALS_DO_NOT_USE_OR_YOU_WILL_BE_FIRED = sl;
    reactDom_production_min.createPortal = function(a, b) {
      var c = 2 < arguments.length && void 0 !== arguments[2] ? arguments[2] : null;
      if (!nl(b)) throw Error(p(200));
      return cl(a, b, null, c);
    };
    reactDom_production_min.createRoot = function(a, b) {
      if (!nl(a)) throw Error(p(299));
      var c = false, d = "", e = kl;
      null !== b && void 0 !== b && (true === b.unstable_strictMode && (c = true), void 0 !== b.identifierPrefix && (d = b.identifierPrefix), void 0 !== b.onRecoverableError && (e = b.onRecoverableError));
      b = bl(a, 1, false, null, null, c, false, d, e);
      a[uf] = b.current;
      sf(8 === a.nodeType ? a.parentNode : a);
      return new ll(b);
    };
    reactDom_production_min.findDOMNode = function(a) {
      if (null == a) return null;
      if (1 === a.nodeType) return a;
      var b = a._reactInternals;
      if (void 0 === b) {
        if ("function" === typeof a.render) throw Error(p(188));
        a = Object.keys(a).join(",");
        throw Error(p(268, a));
      }
      a = Zb(b);
      a = null === a ? null : a.stateNode;
      return a;
    };
    reactDom_production_min.flushSync = function(a) {
      return Rk(a);
    };
    reactDom_production_min.hydrate = function(a, b, c) {
      if (!ol(b)) throw Error(p(200));
      return rl(null, a, b, true, c);
    };
    reactDom_production_min.hydrateRoot = function(a, b, c) {
      if (!nl(a)) throw Error(p(405));
      var d = null != c && c.hydratedSources || null, e = false, f = "", g = kl;
      null !== c && void 0 !== c && (true === c.unstable_strictMode && (e = true), void 0 !== c.identifierPrefix && (f = c.identifierPrefix), void 0 !== c.onRecoverableError && (g = c.onRecoverableError));
      b = el(b, null, a, 1, null != c ? c : null, e, false, f, g);
      a[uf] = b.current;
      sf(a);
      if (d) for (a = 0; a < d.length; a++) c = d[a], e = c._getVersion, e = e(c._source), null == b.mutableSourceEagerHydrationData ? b.mutableSourceEagerHydrationData = [c, e] : b.mutableSourceEagerHydrationData.push(
        c,
        e
      );
      return new ml(b);
    };
    reactDom_production_min.render = function(a, b, c) {
      if (!ol(b)) throw Error(p(200));
      return rl(null, a, b, false, c);
    };
    reactDom_production_min.unmountComponentAtNode = function(a) {
      if (!ol(a)) throw Error(p(40));
      return a._reactRootContainer ? (Rk(function() {
        rl(null, null, a, false, function() {
          a._reactRootContainer = null;
          a[uf] = null;
        });
      }), true) : false;
    };
    reactDom_production_min.unstable_batchedUpdates = Qk;
    reactDom_production_min.unstable_renderSubtreeIntoContainer = function(a, b, c, d) {
      if (!ol(c)) throw Error(p(200));
      if (null == a || void 0 === a._reactInternals) throw Error(p(38));
      return rl(a, b, c, false, d);
    };
    reactDom_production_min.version = "18.3.1-next-f1338f8080-20240426";
    return reactDom_production_min;
  }
  var hasRequiredReactDom;
  function requireReactDom() {
    if (hasRequiredReactDom) return reactDom.exports;
    hasRequiredReactDom = 1;
    function checkDCE() {
      if (typeof __REACT_DEVTOOLS_GLOBAL_HOOK__ === "undefined" || typeof __REACT_DEVTOOLS_GLOBAL_HOOK__.checkDCE !== "function") {
        return;
      }
      try {
        __REACT_DEVTOOLS_GLOBAL_HOOK__.checkDCE(checkDCE);
      } catch (err) {
        console.error(err);
      }
    }
    {
      checkDCE();
      reactDom.exports = requireReactDom_production_min();
    }
    return reactDom.exports;
  }
  var reactDomExports = requireReactDom();
  var client = {};
  var hasRequiredClient;
  function requireClient() {
    if (hasRequiredClient) return client;
    hasRequiredClient = 1;
    var m = requireReactDom();
    {
      client.createRoot = m.createRoot;
      client.hydrateRoot = m.hydrateRoot;
    }
    return client;
  }
  var clientExports = requireClient();
  function finiteNum(val, fallback = null) {
    if (val === null || val === void 0 || typeof val === "boolean" || typeof val === "object" || typeof val === "symbol" || typeof val === "string" && val.trim() === "") {
      return fallback;
    }
    const n = Number(val);
    return Number.isFinite(n) ? n : fallback;
  }
  function clampPct$1(val) {
    const n = finiteNum(val, 0);
    return Math.max(0, Math.min(100, n));
  }
  function pad2Digits(n) {
    return String(Math.max(0, Math.trunc(n))).padStart(2, "0");
  }
  function fmtInt(n) {
    if (n === null || n === void 0 || n === "") return "0";
    const v = finiteNum(n, null);
    if (v === null) return "—";
    const clean = v === 0 ? 0 : v;
    const formatted = clean.toLocaleString("en-US");
    return formatted === "-0" ? "0" : formatted;
  }
  function fmtRate(n) {
    if (n === null || n === void 0 || n === "") return "0/h";
    const raw = finiteNum(n, null);
    if (raw === null) return "—/h";
    const rounded = Math.round(raw);
    const v = rounded === 0 ? 0 : rounded;
    const sign = v > 0 ? "+" : "";
    return `${sign}${fmtInt(v)}/h`;
  }
  function fmtCompact(val) {
    const n = finiteNum(val, null);
    if (n === null) return "—";
    const absN = Math.abs(n);
    if (absN >= 1e6) return `${(n / 1e6).toFixed(2)}M`;
    if (absN >= 1e4) return `${(n / 1e3).toFixed(1)}k`;
    if (Number.isInteger(n)) return fmtInt(n);
    return absN < 0.05 ? "0.0" : n.toFixed(1);
  }
  function fmtImprovementPct$1(rawPct, baseFit, bestFit) {
    let pct = finiteNum(rawPct, null);
    const base = finiteNum(baseFit, null);
    const best = finiteNum(bestFit, null);
    if (pct === null && base !== null && best !== null) {
      if (Math.abs(base) > 1e-6) {
        pct = (best - base) / Math.abs(base) * 100;
      } else if (Math.abs(best) <= 1e-12) {
        pct = 0;
      } else {
        pct = best > 0 ? 999.9 : -999.9;
      }
    }
    if (pct === null) return "—";
    if (pct > 999.9) return ">+999.9%";
    if (pct < -999.9) return "<-999.9%";
    if (Math.abs(pct) < 0.05) pct = 0;
    return `${pct >= 0 ? "+" : ""}${pct.toFixed(1)}%`;
  }
  function fmtHms(seconds) {
    const n = finiteNum(seconds, null);
    if (n === null) return "—";
    const s = Math.max(0, Math.round(n));
    if (s <= 0) return "00:00:00";
    if (s >= 86400) {
      const d = Math.floor(s / 86400);
      const rem = s % 86400;
      const h2 = Math.floor(rem / 3600);
      const m2 = Math.floor(rem % 3600 / 60);
      const sec2 = rem % 60;
      return `${d}d ${pad2Digits(h2)}:${pad2Digits(m2)}:${pad2Digits(sec2)}`;
    }
    const h = Math.floor(s / 3600);
    const m = Math.floor(s % 3600 / 60);
    const sec = s % 60;
    return `${pad2Digits(h)}:${pad2Digits(m)}:${pad2Digits(sec)}`;
  }
  const formatCountdownHms = fmtHms;
  function fmtDurationCountdown(secVal) {
    const sec = Math.max(0, Math.round(finiteNum(secVal, 0)));
    if (sec <= 0) return "Expired";
    const h = Math.floor(sec / 3600);
    const m = Math.floor(sec % 3600 / 60);
    const s = sec % 60;
    if (h > 0) return `${h}h ${String(m).padStart(2, "0")}m`;
    if (m > 0) return `${m}m ${String(s).padStart(2, "0")}s`;
    return `${s}s`;
  }
  function fmtClockTime(finishTs, nowEpochSec) {
    const ts = finiteNum(finishTs, null);
    if (ts === null || ts <= 0) return "—";
    const d = new Date(ts * 1e3);
    if (Number.isNaN(d.getTime())) return "—";
    const refMs = nowEpochSec != null && Number.isFinite(nowEpochSec) && nowEpochSec > 0 ? nowEpochSec * 1e3 : Date.now();
    const now = new Date(refMs);
    const hms = `${pad2Digits(d.getHours())}:${pad2Digits(d.getMinutes())}:${pad2Digits(d.getSeconds())}`;
    const sameDay = d.getFullYear() === now.getFullYear() && d.getMonth() === now.getMonth() && d.getDate() === now.getDate();
    if (sameDay) return hms;
    return `${pad2Digits(d.getMonth() + 1)}-${pad2Digits(d.getDate())} ${hms}`;
  }
  const formatFinishClock = fmtClockTime;
  function fmtClock$1(tsSec) {
    if (!tsSec) return "SYNC --:--:--";
    const ts = finiteNum(tsSec, null);
    if (ts === null) return "SYNC ACTIVE";
    const d = new Date(ts * 1e3);
    if (Number.isNaN(d.getTime())) return "SYNC ACTIVE";
    return `SYNC ${d.toTimeString().slice(0, 8)}`;
  }
  function fmtShortDateTime(tsSec, fallbackTxt = "—") {
    const ts = finiteNum(tsSec, null);
    if (ts !== null && ts > 0) {
      const d = new Date(ts * 1e3);
      if (!Number.isNaN(d.getTime())) {
        const mm = String(d.getMonth() + 1).padStart(2, "0");
        const dd = String(d.getDate()).padStart(2, "0");
        const hh = String(d.getHours()).padStart(2, "0");
        const mi = String(d.getMinutes()).padStart(2, "0");
        return `${mm}-${dd} ${hh}:${mi}`;
      }
    }
    return fallbackTxt && String(fallbackTxt).trim() ? String(fallbackTxt).trim() : "—";
  }
  function formatResourcesInline(res) {
    if (!res) return "—";
    const parts = [];
    if (res.pig_iron) parts.push(`${fmtInt(res.pig_iron)} Pig`);
    if (res.metal) parts.push(`${fmtInt(res.metal)} Met`);
    if (res.kryptonite) parts.push(`${fmtInt(res.kryptonite)} Kryp`);
    if (res.spice) parts.push(`${fmtInt(res.spice)} Spi`);
    return parts.join(" · ") || "0";
  }
  function formatUnitDictShort(obj, limit = 4) {
    if (!obj || typeof obj !== "object") return "None";
    const entries = Object.entries(obj).map(([k, v]) => [String(k), Math.round(finiteNum(v, 0))]).filter(([, v]) => v > 0);
    if (entries.length === 0) return "None";
    const head = entries.slice(0, limit).map(([k, v]) => `${k}:${fmtInt(v)}`).join(" · ");
    return entries.length > limit ? `${head} (+${entries.length - limit})` : head;
  }
  function bottleneckTag(code) {
    if (code == null) return "";
    const norm = String(code).trim().toLowerCase();
    if (!norm) return "";
    if (norm === "pig_iron" || norm === "pig") return "PIG";
    if (norm === "metal" || norm === "met") return "MET";
    if (norm === "kryptonite" || norm === "kryp" || norm === "kry") return "KRY";
    if (norm === "spice" || norm === "spi") return "SPI";
    return norm.toUpperCase();
  }
  function gradeClassFor(gradeRaw) {
    const g = String(gradeRaw || "?").trim().toUpperCase();
    if (g === "S") return "grade-s";
    if (g === "A+") return "grade-aplus";
    if (g === "A") return "grade-a";
    if (g === "B") return "grade-b";
    if (g === "C") return "grade-c";
    if (g === "D") return "grade-d";
    return "grade-unknown";
  }
  function geneShortTag$1(kind) {
    const k = String(kind || "").trim().toUpperCase();
    if (!k) return "";
    if (k === "BUILD") return "BLD";
    if (k === "DEMOLISH_BUILDING") return "DEM";
    if (k === "WAIT_RESOURCES") return "WAIT";
    if (k === "RESEARCH") return "RES";
    if (k === "BUILD_SHIP") return "SHP";
    if (k === "ATTACK") return "ATK";
    if (k === "SPY") return "SPY";
    if (k === "RECYCLE") return "REC";
    if (k === "COLONIZE") return "COL";
    if (k === "SCAN_GALAXY") return "SCN";
    if (k === "THROTTLE_BUILDING") return "B2%";
    if (k === "CLAIM_TUTORIAL") return "TUT";
    return k.slice(0, 4);
  }
  function activePlanetFrom(dash, selectedPlanetId) {
    var _a;
    const gs = dash == null ? void 0 : dash.state;
    if (!gs || !Array.isArray(gs.planets) || gs.planets.length === 0) return null;
    const fallbackSelected = selectedPlanetId !== void 0 ? selectedPlanetId : finiteNum((_a = globalThis == null ? void 0 : globalThis.state) == null ? void 0 : _a.selectedPlanetId, null);
    const targetId = fallbackSelected || gs.active_planet_id;
    return gs.planets.find((p) => p.planet_id === targetId) || gs.planets[0] || null;
  }
  function getSelectedBlastFurnacePct(dash, selectedPlanetId, buildingsEtaOverride) {
    var _a, _b, _c, _d, _e;
    const planet = activePlanetFrom(dash, selectedPlanetId);
    let eta = (dash == null ? void 0 : dash.buildings_eta) || null;
    const globalDash = (_a = globalThis == null ? void 0 : globalThis.state) == null ? void 0 : _a.dashboard;
    const cachedEta = buildingsEtaOverride !== void 0 ? buildingsEtaOverride : ((_b = globalThis == null ? void 0 : globalThis.state) == null ? void 0 : _b.buildingsEtaData) ?? null;
    if (dash && dash !== globalDash && dash.buildings_eta) {
      eta = dash.buildings_eta;
    } else if (cachedEta && planet && ((_c = cachedEta.planet) == null ? void 0 : _c.planet_id) === planet.planet_id) {
      eta = cachedEta;
    }
    const r2 = finiteNum((_d = planet == null ? void 0 : planet.economy_rates) == null ? void 0 : _d.resource2, null);
    const etaPct = finiteNum((_e = eta == null ? void 0 : eta.planet_economy) == null ? void 0 : _e.blast_furnace_pct, null);
    const pct = r2 !== null ? r2 : etaPct !== null ? etaPct : 100;
    return clampPct$1(Math.round(pct));
  }
  function renderResourceBarDom(key, amount, storage, hourlyRate) {
    if (typeof document === "undefined") return;
    const cap = finiteNum(storage, 0);
    const val = finiteNum(amount, null);
    const ratioPct = cap > 0 && val !== null ? val / cap * 100 : 0;
    const pct = Number.isFinite(ratioPct) ? Math.max(0, Math.min(100, Math.round(ratioPct))) : 0;
    const valNode = document.getElementById(`val-${key}`);
    if (valNode) valNode.textContent = fmtInt(amount ?? 0);
    const capNode = document.getElementById(`cap-${key}`);
    if (capNode) capNode.textContent = cap > 0 ? `/ ${fmtInt(cap)} (${pct}%)` : "—";
    const barNode = document.getElementById(`bar-${key}`);
    if (barNode) {
      barNode.style.transform = `scaleX(${pct / 100})`;
      barNode.classList.toggle("high", pct >= 85);
    }
    const rateNode = document.getElementById(`rate-${key}`);
    if (rateNode) {
      if (hourlyRate == null) {
        rateNode.textContent = "—/h";
        rateNode.className = "track-rate";
      } else {
        const rateNum = finiteNum(hourlyRate, null);
        rateNode.textContent = fmtRate(hourlyRate);
        rateNode.className = `track-rate${rateNum !== null && Math.round(rateNum) < 0 ? " is-neg" : ""}`;
      }
    }
  }
  if (typeof globalThis !== "undefined") {
    const g = globalThis;
    g.finiteNum = finiteNum;
    g.clampPct = clampPct$1;
    g.fmtInt = fmtInt;
    g.fmtRate = fmtRate;
    g.fmtCompact = fmtCompact;
    g.fmtImprovementPct = fmtImprovementPct$1;
    g.fmtHms = fmtHms;
    g.formatCountdownHms = formatCountdownHms;
    g.fmtDurationCountdown = fmtDurationCountdown;
    g.fmtClockTime = fmtClockTime;
    g.formatFinishClock = formatFinishClock;
    g.fmtClock = fmtClock$1;
    g.fmtShortDateTime = fmtShortDateTime;
    g.formatResourcesInline = formatResourcesInline;
    g.formatUnitDictShort = formatUnitDictShort;
    g.bottleneckTag = bottleneckTag;
    g.gradeClassFor = gradeClassFor;
    g.geneShortTag = geneShortTag$1;
    g.activePlanetFrom = activePlanetFrom;
    g.getSelectedBlastFurnacePct = getSelectedBlastFurnacePct;
    g.renderResourceBar = renderResourceBarDom;
  }
  var reactExports = requireReact();
  const React = /* @__PURE__ */ getDefaultExportFromCjs(reactExports);
  function safeFiniteNumber(val, fallback = null) {
    if (val === null || val === void 0 || typeof val === "boolean" || typeof val === "object") {
      return fallback;
    }
    if (typeof val === "string") {
      const trimmed = val.trim();
      if (!trimmed) return fallback;
      const parsed = Number(trimmed);
      return Number.isFinite(parsed) ? parsed : fallback;
    }
    if (typeof val === "number") {
      return Number.isFinite(val) ? val : fallback;
    }
    return fallback;
  }
  async function fetchWithTimeout(url, options = {}, timeoutMs = 15e3) {
    const ctrl = new AbortController();
    const timer = setTimeout(() => ctrl.abort(), timeoutMs);
    try {
      return await fetch(url, { ...options, signal: ctrl.signal, cache: "no-store" });
    } finally {
      clearTimeout(timer);
    }
  }
  function ensureJsonObject(parsed, res, preview = "") {
    if (parsed !== null && typeof parsed === "object" && !Array.isArray(parsed)) {
      return parsed;
    }
    const kind = parsed === null ? "null" : Array.isArray(parsed) ? "array" : typeof parsed;
    const snippet = preview ? `: ${preview}` : "";
    const err = new Error(
      res && !res.ok ? `HTTP ${res.status}${snippet || ` (non-object JSON ${kind})`}` : `Invalid JSON object response (expected object, got ${kind})`
    );
    err.status = res == null ? void 0 : res.status;
    err.rateLimited = (res == null ? void 0 : res.status) === 429;
    throw err;
  }
  async function parseResponseJson(res) {
    if (typeof (res == null ? void 0 : res.text) === "function") {
      const raw = await res.text();
      if (!raw || !raw.trim()) {
        return {};
      }
      let parsed2;
      try {
        parsed2 = JSON.parse(raw);
      } catch {
        const preview2 = raw.replace(/\s+/g, " ").trim().slice(0, 120);
        const err = new Error(
          res.ok ? `Invalid JSON response (${preview2 || "empty"})` : `HTTP ${res.status}${preview2 ? `: ${preview2}` : ""}`
        );
        err.status = res.status;
        err.rateLimited = res.status === 429;
        throw err;
      }
      const preview = raw.replace(/\s+/g, " ").trim().slice(0, 120);
      return ensureJsonObject(parsed2, res, preview);
    }
    let parsed;
    try {
      parsed = await res.json();
    } catch (parseErr) {
      const err = new Error(
        res && !res.ok ? `HTTP ${res.status}` : `Invalid JSON response (${(parseErr == null ? void 0 : parseErr.message) || "parse error"})`
      );
      err.status = res == null ? void 0 : res.status;
      err.rateLimited = (res == null ? void 0 : res.status) === 429;
      throw err;
    }
    return ensureJsonObject(parsed, res);
  }
  function isRateLimitError(err) {
    if (!err) return false;
    if (err.rateLimited || err.status === 429) return true;
    const msg = String(err.message || err || "").toLowerCase();
    return msg.includes("rate-limit") || msg.includes("rate limit") || msg.includes("rate_limit") || msg.includes("cooldown") || msg.includes("30 page views") || msg.includes("too many requests") || msg.includes("retry after");
  }
  function feedbackKindForError(err) {
    return isRateLimitError(err) ? "warn" : "error";
  }
  let bridgeCallbacks = {};
  function registerHttpBridgeCallbacks(cbs) {
    bridgeCallbacks = cbs;
  }
  async function apiGet(url, timeoutMs = 15e3) {
    var _a;
    const res = await fetchWithTimeout(url, {}, timeoutMs);
    const data = ensureJsonObject(await parseResponseJson(res), res);
    if (!res.ok || (data == null ? void 0 : data.ok) === false) {
      const err = new Error((data == null ? void 0 : data.error) || `HTTP ${res.status}`);
      err.status = res.status;
      err.rateLimited = Boolean((data == null ? void 0 : data.rate_limited) || res.status === 429);
      err.retryAfter = safeFiniteNumber(data == null ? void 0 : data.retry_after, 0) ?? 0;
      throw err;
    }
    if ((data == null ? void 0 : data.notifications) && typeof data.notifications === "object") {
      (_a = bridgeCallbacks.onNotifications) == null ? void 0 : _a.call(bridgeCallbacks, data.notifications);
    }
    return data;
  }
  async function apiCommand(payload, opts) {
    var _a, _b, _c;
    const timeoutMs = Number((opts == null ? void 0 : opts.timeoutMs) ?? 22050);
    let res;
    try {
      res = await fetchWithTimeout(
        "/command",
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload)
        },
        timeoutMs
      );
    } catch (err) {
      const msg = (err == null ? void 0 : err.name) === "AbortError" ? "timeout" : (err == null ? void 0 : err.message) || "unreachable";
      (_a = bridgeCallbacks.onStatusChange) == null ? void 0 : _a.call(bridgeCallbacks, false, msg);
      throw err;
    }
    const data = ensureJsonObject(await parseResponseJson(res), res);
    (_b = bridgeCallbacks.onStatusChange) == null ? void 0 : _b.call(bridgeCallbacks, true);
    if (!res.ok || (data == null ? void 0 : data.ok) === false) {
      const err = new Error((data == null ? void 0 : data.error) || `HTTP ${res.status}`);
      err.status = res.status;
      err.rateLimited = Boolean((data == null ? void 0 : data.rate_limited) || res.status === 429);
      err.retryAfter = safeFiniteNumber(data == null ? void 0 : data.retry_after, 0) ?? 0;
      throw err;
    }
    if ((data == null ? void 0 : data.notifications) && typeof data.notifications === "object") {
      (_c = bridgeCallbacks.onNotifications) == null ? void 0 : _c.call(bridgeCallbacks, data.notifications);
    }
    return data;
  }
  const PAGE_TITLES = {
    dashboard: "DASHBOARD // OVERVIEW",
    buildings: "BUILDINGS // LEVEL ETAS",
    combat: "COMBAT & FLEET // 1:1 SIM",
    espionage: "ESPIONAGE // INTEL & ARCHIVE",
    colonies: "COLONIES // 300+ GRADING",
    attacks: "COMBAT // ATTACK RESULTS",
    recyclers: "RECYCLERS // RUBBLE & INBOX",
    operations: "OPERATIONS // PRIORITY QUEUE",
    "action-graph": "ACTION GRAPH // SEQUENCE & DAG",
    ranking: "RANKING // EVOLUTION & AZ",
    config: "STRATEGY // CONFIG BUCKETS",
    logs: "TERMINAL // LIVE LOGS"
  };
  const useClientSyncEffect$2 = typeof window !== "undefined" ? reactExports.useLayoutEffect : reactExports.useEffect;
  const SESSION_STATE_KEY = "si_console_ui_state";
  const DEFAULT_UI_PREFS = {
    modeOverride: "auto",
    bldCategory: "all",
    selectedUnitKey: "b1",
    combatSubTab: "ships",
    queueFilter: "all",
    activeLogSource: "commander",
    agEdgeFilter: "all",
    agParamTab: "core",
    espTab: "targets",
    colonyFilter: "all",
    atkTab: "battles",
    recTab: "harvests",
    emailCatFilter: "all",
    notifFilter: "active",
    sidebarOpen: false
  };
  function createInitialLegacyWindowState() {
    const st = {
      dashboard: null,
      selectedPlanetId: null,
      modeOverride: "auto",
      activePage: "dashboard",
      bldCategory: "all",
      selectedUnitKey: "b1",
      buildingsEtaData: null,
      combatData: null,
      combatSubTab: "ships",
      queueFilter: "all",
      activeLogSource: "commander",
      simPopulatedOnce: false,
      apiOnline: false,
      reconnectAttempts: 0,
      configDirty: false,
      configSaving: 0,
      gaCalcCount: 0,
      lastOptimizerSummary: null,
      inFlightVersionCheck: false,
      inFlightDashboard: false,
      inFlightDashboardCount: 0,
      dashboardSeq: 0,
      actionGraphData: null,
      agSimDirty: false,
      agSimRunning: 0,
      agSimSeq: 0,
      agEdgeFilter: "all",
      agParamTab: "core",
      agSelectedNodeId: null,
      agSuppressNodeClick: false,
      espTab: "targets",
      espData: null,
      colonyFilter: "all",
      colonyData: null,
      atkTab: "battles",
      atkData: null,
      recTab: "harvests",
      emailCatFilter: "all",
      recData: null,
      notifications: { unread_count: 0, items: [] },
      notifFilter: "active",
      activeHelpNotifId: null
    };
    Object.defineProperty(st, "gaCalcRunning", {
      enumerable: true,
      configurable: true,
      get() {
        return st.gaCalcCount > 0;
      },
      set(flag) {
        st.gaCalcCount = flag ? Math.max(1, st.gaCalcCount) : 0;
      }
    });
    return st;
  }
  const sharedLegacyState = createInitialLegacyWindowState();
  if (typeof globalThis !== "undefined") {
    const g = globalThis;
    g.state = sharedLegacyState;
    g.el = (id) => typeof document !== "undefined" ? document.getElementById(id) : null;
    g.apiGet = apiGet;
    g.apiCommand = apiCommand;
    g.parseResponseJson = parseResponseJson;
    g.feedbackKindForError = feedbackKindForError;
    if (typeof window !== "undefined") {
      window.state = sharedLegacyState;
      window.el = g.el;
    }
  }
  function selectActivePlanet(dash, selectedPlanetId) {
    const gs = dash == null ? void 0 : dash.state;
    if (!gs || !Array.isArray(gs.planets) || gs.planets.length === 0) return null;
    const targetId = selectedPlanetId || gs.active_planet_id;
    return gs.planets.find((p) => p.planet_id === targetId) || gs.planets[0] || null;
  }
  function buildClientFallbackActionGraph(dash) {
    var _a;
    const cfg = (dash == null ? void 0 : dash.config) || {};
    const mode = String(cfg.planner_mode || (dash == null ? void 0 : dash.planner_mode) || "priority").toLowerCase() === "ga_nn" ? "ga_nn" : "priority";
    const planList = Array.isArray(dash == null ? void 0 : dash.plan) ? dash.plan : [];
    const planets = Array.isArray((_a = dash == null ? void 0 : dash.state) == null ? void 0 : _a.planets) ? dash.state.planets : [];
    const planetById = /* @__PURE__ */ new Map();
    planets.forEach((p) => {
      const pid = safeFiniteNumber(p == null ? void 0 : p.planet_id, 0) ?? 0;
      if (pid > 0) planetById.set(pid, p);
    });
    const laneCounts = /* @__PURE__ */ new Map([["lane:global", 0]]);
    planets.forEach((p) => {
      const pid = safeFiniteNumber(p == null ? void 0 : p.planet_id, 0) ?? 0;
      if (pid > 0) laneCounts.set(`lane:planet:${pid}`, 0);
    });
    let execRank = 0;
    const nodes = planList.map((act, idx) => {
      var _a2, _b, _c, _d, _e, _f, _g, _h;
      const kind = String((act == null ? void 0 : act.kind) || "BUILD").toUpperCase();
      const pid = Math.max(0, Math.round(safeFiniteNumber(act == null ? void 0 : act.planet_id, 0) ?? 0));
      const pObj = planetById.get(pid) || null;
      const laneId = kind === "RESEARCH" || kind === "CLAIM_TUTORIAL" || pid <= 0 ? "lane:global" : `lane:planet:${pid}`;
      laneCounts.set(laneId, (laneCounts.get(laneId) || 0) + 1);
      const execNow = Boolean(act == null ? void 0 : act.executable_now);
      const missing = {
        pig_iron: Math.max(0, Math.round(safeFiniteNumber((_a2 = act == null ? void 0 : act.missing) == null ? void 0 : _a2.pig_iron, 0) ?? 0)),
        metal: Math.max(0, Math.round(safeFiniteNumber((_b = act == null ? void 0 : act.missing) == null ? void 0 : _b.metal, 0) ?? 0)),
        kryptonite: Math.max(0, Math.round(safeFiniteNumber((_c = act == null ? void 0 : act.missing) == null ? void 0 : _c.kryptonite, 0) ?? 0)),
        spice: Math.max(0, Math.round(safeFiniteNumber((_d = act == null ? void 0 : act.missing) == null ? void 0 : _d.spice, 0) ?? 0))
      };
      const hasMissing = missing.pig_iron > 0 || missing.metal > 0 || missing.kryptonite > 0 || missing.spice > 0;
      const reason = String((act == null ? void 0 : act.reason) || "");
      const status = execNow ? "ready" : kind === "ALERT_HOSTILE" || reason.toLowerCase().includes("[hold:") ? "blocked_gate" : hasMissing || kind === "WAIT_RESOURCES" ? "waiting_resources" : "queued";
      return {
        id: `act_${idx}`,
        order_index: idx,
        exec_rank: execNow ? execRank++ : null,
        topological_layer: idx,
        lane_id: laneId,
        priority: String((act == null ? void 0 : act.priority) || "P2"),
        kind,
        category: kind === "RESEARCH" ? "research" : "economy",
        status,
        planet_id: pid,
        planet_coords: String((act == null ? void 0 : act.planet_coords) || (pObj == null ? void 0 : pObj.coords) || ""),
        planet_name: String((pObj == null ? void 0 : pObj.name) || (pid > 0 ? `Colony ${pid}` : "Empire")),
        target_id: safeFiniteNumber(act == null ? void 0 : act.target_id, null),
        target_name: String((act == null ? void 0 : act.target_name) || kind),
        target_level: safeFiniteNumber(act == null ? void 0 : act.target_level, null),
        reason,
        executable_now: execNow,
        cost: {
          pig_iron: Math.max(0, Math.round(safeFiniteNumber((_e = act == null ? void 0 : act.cost) == null ? void 0 : _e.pig_iron, 0) ?? 0)),
          metal: Math.max(0, Math.round(safeFiniteNumber((_f = act == null ? void 0 : act.cost) == null ? void 0 : _f.metal, 0) ?? 0)),
          kryptonite: Math.max(0, Math.round(safeFiniteNumber((_g = act == null ? void 0 : act.cost) == null ? void 0 : _g.kryptonite, 0) ?? 0)),
          spice: Math.max(0, Math.round(safeFiniteNumber((_h = act == null ? void 0 : act.cost) == null ? void 0 : _h.spice, 0) ?? 0))
        },
        missing,
        wait_seconds: safeFiniteNumber(act == null ? void 0 : act.wait_seconds, 0) ?? 0,
        wait_eta: String((act == null ? void 0 : act.wait_eta) || "Ready"),
        duration_seconds: Math.max(0, safeFiniteNumber(act == null ? void 0 : act.duration_seconds, 0) ?? 0),
        duration_text: String((act == null ? void 0 : act.duration_text) || "Instant"),
        total_seconds: safeFiniteNumber(act == null ? void 0 : act.total_seconds, 0) ?? 0,
        total_eta: String((act == null ? void 0 : act.total_eta) || "Ready"),
        eta_bottleneck: (act == null ? void 0 : act.eta_bottleneck) ? String(act.eta_bottleneck) : null,
        ga_step: null,
        in_degree: idx > 0 ? 1 : 0,
        out_degree: idx + 1 < planList.length ? 1 : 0
      };
    });
    const edges = [];
    for (let i = 0; i + 1 < nodes.length; i += 1) {
      const pType = nodes[i].lane_id === nodes[i + 1].lane_id ? "queue_order" : "execution_order";
      edges.push({
        id: `edge_act_${i}_act_${i + 1}`,
        source: `act_${i}`,
        target: `act_${i + 1}`,
        primary_type: pType,
        types: [pType],
        label: `Planned step #${i + 1} -> #${i + 2}`
      });
    }
    const lanes = [];
    if (planets.length > 0 || nodes.length > 0) {
      lanes.push({
        id: "lane:global",
        scope: "global",
        label: "Empire & Global Research",
        planet_id: 0,
        coords: "",
        planet_type: "global",
        energy: 0,
        node_count: laneCounts.get("lane:global") || 0
      });
      planets.forEach((p) => {
        var _a2, _b;
        const pid = Math.max(0, Math.round(safeFiniteNumber(p == null ? void 0 : p.planet_id, 0) ?? 0));
        if (pid <= 0) return;
        const lid = `lane:planet:${pid}`;
        lanes.push({
          id: lid,
          scope: "planet",
          label: (p == null ? void 0 : p.coords) ? `${p.name || `Colony ${pid}`} [${p.coords}]` : String((p == null ? void 0 : p.name) || `Colony ${pid}`),
          planet_id: pid,
          coords: String((p == null ? void 0 : p.coords) || ""),
          planet_type: String((p == null ? void 0 : p.planet_type) || "normal"),
          energy: Math.round(
            (safeFiniteNumber((_a2 = p == null ? void 0 : p.energy) == null ? void 0 : _a2.produced, 0) ?? 0) - (safeFiniteNumber((_b = p == null ? void 0 : p.energy) == null ? void 0 : _b.used, 0) ?? 0)
          ),
          node_count: laneCounts.get(lid) || 0
        });
      });
    }
    return {
      mode,
      simulated: false,
      state_available: Boolean(dash == null ? void 0 : dash.state),
      is_dirty: false,
      changed_keys: [],
      active_config: cfg,
      effective_config: cfg,
      lanes,
      nodes,
      edges,
      summary: {
        total_nodes: nodes.length,
        total_edges: edges.length,
        ready_count: nodes.filter((n) => n.status === "ready").length,
        waiting_count: nodes.filter((n) => n.status === "waiting_resources").length,
        blocked_count: nodes.filter((n) => n.status === "blocked_gate").length,
        queued_count: nodes.filter((n) => n.status === "queued").length,
        max_layer: Math.max(0, nodes.length - 1),
        critical_path_eta: nodes.length > 0 ? String(nodes[nodes.length - 1].total_eta || "Ready") : "Ready",
        primary_bottleneck: null,
        by_priority: { P0: 0, P1: 0, P2: nodes.length, P3: 0 },
        by_edge_type: {}
      },
      ga_summary: (dash == null ? void 0 : dash.optimizer_summary) || null
    };
  }
  const ConsoleContext = reactExports.createContext(null);
  function readInitialPersistedState() {
    let activePage = "dashboard";
    let selectedPlanetId = null;
    const prefs = { ...DEFAULT_UI_PREFS };
    if (typeof window === "undefined") {
      return { activePage, selectedPlanetId, prefs };
    }
    try {
      const raw = sessionStorage.getItem(SESSION_STATE_KEY);
      if (raw) {
        const saved = JSON.parse(raw);
        if (saved && typeof saved === "object" && !Array.isArray(saved)) {
          const savedPid = safeFiniteNumber(saved.selectedPlanetId, null);
          if (savedPid !== null) selectedPlanetId = savedPid;
          if (saved.bldCategory) prefs.bldCategory = String(saved.bldCategory);
          if (saved.selectedUnitKey) prefs.selectedUnitKey = String(saved.selectedUnitKey);
          if (saved.combatSubTab) prefs.combatSubTab = String(saved.combatSubTab);
          if (saved.queueFilter) prefs.queueFilter = String(saved.queueFilter);
          if (saved.activeLogSource) prefs.activeLogSource = String(saved.activeLogSource);
          if (saved.agEdgeFilter === "all" || saved.agEdgeFilter === "deps") {
            prefs.agEdgeFilter = saved.agEdgeFilter;
          }
          if (saved.agParamTab === "core" || saved.agParamTab === "scope") {
            prefs.agParamTab = saved.agParamTab;
          }
          if (saved.activePage && PAGE_TITLES[saved.activePage]) {
            activePage = saved.activePage;
          }
        }
      }
    } catch {
    }
    const rawHash = (window.location.hash || "").replace(/^#/, "");
    const normHash = rawHash === "graph" ? "action-graph" : rawHash;
    if (normHash && PAGE_TITLES[normHash]) {
      activePage = normHash;
    }
    return { activePage, selectedPlanetId, prefs };
  }
  const ConsoleProvider = ({
    children,
    disableInitialFetch = false
  }) => {
    var _a;
    const initial = reactExports.useMemo(() => readInitialPersistedState(), []);
    const [dashboard, setDashboard] = reactExports.useState(null);
    const [apiOnline, setApiOnlineState] = reactExports.useState(false);
    const [reconnectAttempts, setReconnectAttempts] = reactExports.useState(0);
    const [activePage, setActivePageState] = reactExports.useState(initial.activePage);
    const [selectedPlanetId, setSelectedPlanetIdState] = reactExports.useState(
      initial.selectedPlanetId
    );
    const [nowEpochSec, setNowEpochSec] = reactExports.useState(
      () => typeof window === "undefined" ? 0 : Math.floor(Date.now() / 1e3)
    );
    const [gaCalcCount, setGaCalcCount] = reactExports.useState(0);
    const [feedback, setFeedback] = reactExports.useState(null);
    const [uiPrefs, setUiPrefs] = reactExports.useState(initial.prefs);
    const [buildingsEtaData, setBuildingsEtaDataState] = reactExports.useState(null);
    const [combatData, setCombatDataState] = reactExports.useState(null);
    const [espData, setEspDataState] = reactExports.useState(null);
    const [colonyData, setColonyDataState] = reactExports.useState(null);
    const [atkData, setAtkDataState] = reactExports.useState(null);
    const [recData, setRecDataState] = reactExports.useState(null);
    const [actionGraphData, setActionGraphDataState] = reactExports.useState(null);
    const [agSimDirty, setAgSimDirtyState] = reactExports.useState(false);
    const [agSimRunning, setAgSimRunning] = reactExports.useState(0);
    const [configDirty, setConfigDirtyState] = reactExports.useState(false);
    const [configSaving, setConfigSaving] = reactExports.useState(0);
    const [configHydrationTick, setConfigHydrationTick] = reactExports.useState(0);
    const [agHydrationTick, setAgHydrationTick] = reactExports.useState(0);
    const [lastOptimizerSummary, setLastOptimizerSummaryState] = reactExports.useState(
      null
    );
    const [notifications, setNotifications] = reactExports.useState({
      unread_count: 0,
      items: []
    });
    const [notifCenterOpen, setNotifCenterOpen] = reactExports.useState(false);
    const [activeHelpNotif, setActiveHelpNotif] = reactExports.useState(null);
    const feedbackTimerRef = reactExports.useRef(null);
    const uiVersionsRef = reactExports.useRef({
      code_version: null,
      css_version: null,
      server_boot_id: null
    });
    const seenHelpSigsRef = reactExports.useRef(/* @__PURE__ */ new Set());
    const notifCenterOpenRef = reactExports.useRef(false);
    notifCenterOpenRef.current = notifCenterOpen;
    const uiPrefsRef = reactExports.useRef(uiPrefs);
    uiPrefsRef.current = uiPrefs;
    sharedLegacyState.dashboard = dashboard;
    sharedLegacyState.apiOnline = apiOnline;
    sharedLegacyState.reconnectAttempts = reconnectAttempts;
    sharedLegacyState.activePage = activePage;
    sharedLegacyState.selectedPlanetId = selectedPlanetId;
    sharedLegacyState.modeOverride = uiPrefs.modeOverride;
    sharedLegacyState.bldCategory = uiPrefs.bldCategory;
    sharedLegacyState.selectedUnitKey = uiPrefs.selectedUnitKey;
    sharedLegacyState.combatSubTab = uiPrefs.combatSubTab;
    sharedLegacyState.queueFilter = uiPrefs.queueFilter;
    sharedLegacyState.activeLogSource = uiPrefs.activeLogSource;
    sharedLegacyState.agEdgeFilter = uiPrefs.agEdgeFilter;
    sharedLegacyState.agParamTab = uiPrefs.agParamTab;
    sharedLegacyState.espTab = uiPrefs.espTab;
    sharedLegacyState.colonyFilter = uiPrefs.colonyFilter;
    sharedLegacyState.atkTab = uiPrefs.atkTab;
    sharedLegacyState.recTab = uiPrefs.recTab;
    sharedLegacyState.emailCatFilter = uiPrefs.emailCatFilter;
    sharedLegacyState.notifFilter = uiPrefs.notifFilter;
    sharedLegacyState.buildingsEtaData = buildingsEtaData;
    sharedLegacyState.combatData = combatData;
    sharedLegacyState.espData = espData;
    sharedLegacyState.colonyData = colonyData;
    sharedLegacyState.atkData = atkData;
    sharedLegacyState.recData = recData;
    sharedLegacyState.actionGraphData = actionGraphData;
    sharedLegacyState.agSimDirty = agSimDirty;
    sharedLegacyState.agSimRunning = agSimRunning;
    sharedLegacyState.gaCalcCount = gaCalcCount;
    sharedLegacyState.lastOptimizerSummary = lastOptimizerSummary;
    sharedLegacyState.notifications = notifications;
    sharedLegacyState.activeHelpNotifId = activeHelpNotif ? safeFiniteNumber(activeHelpNotif.id, null) : null;
    const persistUiPrefs = reactExports.useCallback(
      (nextPage, nextPid, nextPrefs) => {
        if (typeof window === "undefined") return;
        try {
          sessionStorage.setItem(
            SESSION_STATE_KEY,
            JSON.stringify({
              activePage: nextPage,
              selectedPlanetId: nextPid,
              bldCategory: nextPrefs.bldCategory,
              selectedUnitKey: nextPrefs.selectedUnitKey,
              combatSubTab: nextPrefs.combatSubTab,
              queueFilter: nextPrefs.queueFilter,
              activeLogSource: nextPrefs.activeLogSource,
              agEdgeFilter: nextPrefs.agEdgeFilter,
              agParamTab: nextPrefs.agParamTab
            })
          );
        } catch {
        }
      },
      []
    );
    const showFeedback = reactExports.useCallback(
      (message, kind = "ok", opts = {}) => {
        if (feedbackTimerRef.current) {
          clearTimeout(feedbackTimerRef.current);
          feedbackTimerRef.current = null;
        }
        if (typeof document !== "undefined") {
          const banner = document.getElementById("feedback-banner");
          if (banner) {
            const cls = kind === "error" ? "is-error" : kind === "warn" ? "is-warn" : "is-ok";
            banner.className = `feedback-banner ${cls}`;
            banner.hidden = false;
            banner.textContent = message;
          }
        }
        setFeedback({ message, kind, hidden: false });
        const autoHideMs = opts.autoHideMs ?? 0;
        if (autoHideMs > 0) {
          feedbackTimerRef.current = setTimeout(() => {
            if (sharedLegacyState.apiOnline) {
              if (typeof document !== "undefined") {
                const banner = document.getElementById("feedback-banner");
                if (banner) banner.hidden = true;
              }
              setFeedback((prev) => prev ? { ...prev, hidden: true } : null);
            }
          }, autoHideMs);
        }
      },
      []
    );
    const setApiStatus = reactExports.useCallback(
      (online, note = "") => {
        var _a2, _b;
        const wasOnline = sharedLegacyState.apiOnline;
        sharedLegacyState.apiOnline = online;
        setApiOnlineState(online);
        const port = ((_b = (_a2 = sharedLegacyState.dashboard) == null ? void 0 : _a2.health) == null ? void 0 : _b.debug_port) ? "8787" : typeof window !== "undefined" && window.location.port || "8787";
        if (online) {
          const recovered = !wasOnline && sharedLegacyState.reconnectAttempts > 0;
          sharedLegacyState.reconnectAttempts = 0;
          setReconnectAttempts(0);
          if (recovered) {
            showFeedback(`Reconnected to Game API :${port}.`, "ok", { autoHideMs: 3200 });
          }
        } else {
          sharedLegacyState.reconnectAttempts += 1;
          setReconnectAttempts(sharedLegacyState.reconnectAttempts);
          showFeedback(
            `API offline (${note || "connection lost"}) — auto-reconnecting every 1.2s...`,
            "warn"
          );
        }
      },
      [showFeedback]
    );
    const syncNotificationsPayload = reactExports.useCallback(
      (notifPayload) => {
        if (!notifPayload || typeof notifPayload !== "object") return;
        const items = Array.isArray(notifPayload.items) ? notifPayload.items : [];
        const unreadCnt = Math.max(0, Math.round(safeFiniteNumber(notifPayload.unread_count, 0) ?? 0));
        const next = { unread_count: unreadCnt, items };
        sharedLegacyState.notifications = next;
        setNotifications(next);
        const newestUnread = items.find(
          (n) => n && n.status === "unread" && (safeFiniteNumber(n.id, 0) ?? 0) > 0
        );
        if (newestUnread) {
          const sig = `${newestUnread.id}:${safeFiniteNumber(newestUnread.occurrence_count, 1) ?? 1}`;
          if (!seenHelpSigsRef.current.has(sig) && !notifCenterOpenRef.current) {
            seenHelpSigsRef.current.add(sig);
            sharedLegacyState.activeHelpNotifId = safeFiniteNumber(newestUnread.id, 0);
            setActiveHelpNotif(newestUnread);
          }
        } else if (sharedLegacyState.activeHelpNotifId !== null) {
          sharedLegacyState.activeHelpNotifId = null;
          setActiveHelpNotif(null);
        }
      },
      []
    );
    useClientSyncEffect$2(() => {
      registerHttpBridgeCallbacks({
        onStatusChange: setApiStatus,
        onNotifications: syncNotificationsPayload
      });
    }, [setApiStatus, syncNotificationsPayload]);
    const beginGaCalc = reactExports.useCallback(() => {
      sharedLegacyState.gaCalcCount += 1;
      if (typeof document !== "undefined") {
        const hud = document.getElementById("gann-calc-hud");
        if (hud) hud.classList.add("is-calculating");
      }
      setGaCalcCount(sharedLegacyState.gaCalcCount);
    }, []);
    const endGaCalc = reactExports.useCallback(() => {
      sharedLegacyState.gaCalcCount = Math.max(0, sharedLegacyState.gaCalcCount - 1);
      if (typeof document !== "undefined" && sharedLegacyState.gaCalcCount === 0) {
        const hud = document.getElementById("gann-calc-hud");
        if (hud) hud.classList.remove("is-calculating");
      }
      setGaCalcCount(sharedLegacyState.gaCalcCount);
    }, []);
    const setConfigDirty = reactExports.useCallback((dirty) => {
      const flag = Boolean(dirty);
      sharedLegacyState.configDirty = flag;
      setConfigDirtyState(flag);
    }, []);
    const setAgSimDirty = reactExports.useCallback((dirty) => {
      const flag = Boolean(dirty);
      sharedLegacyState.agSimDirty = flag;
      setAgSimDirtyState(flag);
    }, []);
    const setBuildingsEtaData = reactExports.useCallback((data) => {
      sharedLegacyState.buildingsEtaData = data;
      setBuildingsEtaDataState(data);
    }, []);
    const setCombatData = reactExports.useCallback((data) => {
      sharedLegacyState.combatData = data;
      setCombatDataState(data);
    }, []);
    const setEspData = reactExports.useCallback((data) => {
      sharedLegacyState.espData = data;
      setEspDataState(data);
    }, []);
    const setColonyData = reactExports.useCallback((data) => {
      sharedLegacyState.colonyData = data;
      setColonyDataState(data);
    }, []);
    const setAtkData = reactExports.useCallback((data) => {
      sharedLegacyState.atkData = data;
      setAtkDataState(data);
    }, []);
    const setRecData = reactExports.useCallback((data) => {
      sharedLegacyState.recData = data;
      setRecDataState(data);
    }, []);
    const setActionGraphData = reactExports.useCallback((data) => {
      sharedLegacyState.actionGraphData = data;
      setActionGraphDataState(data);
    }, []);
    const setLastOptimizerSummary = reactExports.useCallback((summary) => {
      sharedLegacyState.lastOptimizerSummary = summary;
      setLastOptimizerSummaryState(summary);
    }, []);
    const bumpDashboardSeq = reactExports.useCallback(() => {
      sharedLegacyState.dashboardSeq += 1;
      return sharedLegacyState.dashboardSeq;
    }, []);
    const applyVersionSnapshot = reactExports.useCallback(
      (vData) => {
        var _a2, _b;
        if (!vData) return false;
        const prev = uiVersionsRef.current;
        if (prev.code_version == null) {
          uiVersionsRef.current = {
            code_version: vData.code_version || null,
            css_version: vData.css_version || null,
            server_boot_id: vData.server_boot_id || null
          };
          return false;
        }
        const codeChanged = Boolean(vData.code_version && vData.code_version !== prev.code_version);
        const bootChanged = Boolean(
          vData.server_boot_id && prev.server_boot_id && vData.server_boot_id !== prev.server_boot_id
        );
        if ((_b = (_a2 = sharedLegacyState.dashboard) == null ? void 0 : _a2.bot_control) == null ? void 0 : _b.captcha_blocked) {
          return false;
        }
        if (codeChanged || bootChanged) {
          persistUiPrefs(
            sharedLegacyState.activePage,
            sharedLegacyState.selectedPlanetId,
            uiPrefsRef.current
          );
          showFeedback(
            codeChanged ? "UI update detected — hot-reloading console..." : "Game API restarted — hot-reloading console...",
            "ok"
          );
          if (typeof window !== "undefined") {
            setTimeout(() => window.location.reload(), 120);
          }
          return true;
        }
        if (vData.css_version && vData.css_version !== prev.css_version) {
          uiVersionsRef.current.css_version = vData.css_version;
          if (typeof document !== "undefined") {
            const link = document.getElementById("ui-stylesheet");
            if (link) {
              link.href = `/styles.css?v=${encodeURIComponent(vData.css_version)}`;
            }
          }
          showFeedback("Hot-reloaded styles.css live.", "ok", { autoHideMs: 2600 });
        }
        return false;
      },
      [persistUiPrefs, showFeedback]
    );
    const refreshBuildingsEtaForSelectedPlanet = reactExports.useCallback(async (forceRefresh = false) => {
      const planet = selectActivePlanet(sharedLegacyState.dashboard, sharedLegacyState.selectedPlanetId);
      if (!planet) return;
      try {
        const res = await apiCommand({
          command: "buildings_eta",
          planet_id: planet.planet_id,
          refresh: forceRefresh
        });
        if (res.buildings_eta) {
          setBuildingsEtaData(res.buildings_eta);
        }
      } catch {
      }
    }, [setBuildingsEtaData]);
    const refreshCombatForSelectedPlanet = reactExports.useCallback(
      async ({ refresh = false } = {}) => {
        const planet = selectActivePlanet(
          sharedLegacyState.dashboard,
          sharedLegacyState.selectedPlanetId
        );
        if (!planet) return false;
        try {
          const res = await apiCommand({
            command: "combat_overview",
            planet_id: planet.planet_id,
            refresh
          });
          if (res.combat) {
            setCombatData(res.combat);
          }
          return true;
        } catch (err) {
          if (refresh || isRateLimitError(err)) {
            showFeedback(`Shipyard refresh failed: ${err.message}`, feedbackKindForError(err));
          }
          return false;
        }
      },
      [setCombatData, showFeedback]
    );
    const refreshEspionageView = reactExports.useCallback(async () => {
      try {
        const data = await apiCommand({ command: "espionage_intel", limit: 200 });
        setEspData(data);
      } catch (err) {
        showFeedback(`Espionage data load failed: ${err.message}`, feedbackKindForError(err));
      }
    }, [setEspData, showFeedback]);
    const refreshColoniesView = reactExports.useCallback(async () => {
      try {
        const data = await apiCommand({ command: "colony_candidates", limit: 200 });
        setColonyData(data);
      } catch (err) {
        showFeedback(`Colony grading load failed: ${err.message}`, feedbackKindForError(err));
      }
    }, [setColonyData, showFeedback]);
    const refreshAttacksView = reactExports.useCallback(async () => {
      try {
        const data = await apiCommand({ command: "attack_results", limit: 200 });
        setAtkData(data);
      } catch (err) {
        showFeedback(`Attack results load failed: ${err.message}`, feedbackKindForError(err));
      }
    }, [setAtkData, showFeedback]);
    const refreshRecyclersView = reactExports.useCallback(
      async (categoryOverride) => {
        try {
          const cat = categoryOverride ?? sharedLegacyState.emailCatFilter ?? "all";
          const data = await apiCommand({
            command: "recycler_results",
            category: cat,
            limit: 200
          });
          setRecData(data);
        } catch (err) {
          showFeedback(`Recycler and email load failed: ${err.message}`, feedbackKindForError(err));
        }
      },
      [setRecData, showFeedback]
    );
    const loadDashboard = reactExports.useCallback(
      async ({
        refresh = false,
        preserveConfig = true,
        force = false
      } = {}) => {
        var _a2, _b, _c;
        if (!refresh && !force && sharedLegacyState.inFlightDashboard) return false;
        const reqSeq = ++sharedLegacyState.dashboardSeq;
        sharedLegacyState.inFlightDashboardCount += 1;
        sharedLegacyState.inFlightDashboard = true;
        try {
          const dash = await apiGet(
            `/dashboard?refresh=${refresh ? 1 : 0}`,
            15e3
          );
          if (reqSeq !== sharedLegacyState.dashboardSeq) return false;
          if (applyVersionSnapshot(dash)) return false;
          setApiStatus(true);
          const preserveUnsavedCfg = (sharedLegacyState.configDirty || sharedLegacyState.configSaving > 0) && preserveConfig;
          const nextDash = preserveUnsavedCfg && ((_a2 = sharedLegacyState.dashboard) == null ? void 0 : _a2.config) ? {
            ...dash,
            config: sharedLegacyState.dashboard.config,
            planner_mode: sharedLegacyState.dashboard.planner_mode || dash.planner_mode
          } : dash;
          sharedLegacyState.dashboard = nextDash;
          setDashboard(nextDash);
          if (nextDash.optimizer_summary) {
            setLastOptimizerSummary(nextDash.optimizer_summary);
          }
          const modeVal = ((_b = nextDash.bot_control) == null ? void 0 : _b.mode_override) || "auto";
          setUiPrefs(
            (prev) => prev.modeOverride === modeVal ? prev : { ...prev, modeOverride: modeVal }
          );
          if (nextDash.buildings_eta) {
            if (!sharedLegacyState.selectedPlanetId || sharedLegacyState.selectedPlanetId === ((_c = nextDash.buildings_eta.planet) == null ? void 0 : _c.planet_id)) {
              setBuildingsEtaData(nextDash.buildings_eta);
            }
          }
          if (nextDash.combat) {
            if (!sharedLegacyState.selectedPlanetId || sharedLegacyState.selectedPlanetId === nextDash.combat.planet_id) {
              setCombatData(nextDash.combat);
            }
          }
          if (nextDash.notifications && typeof nextDash.notifications === "object") {
            syncNotificationsPayload(nextDash.notifications);
          }
          const curPage = sharedLegacyState.activePage;
          if (curPage === "espionage") {
            refreshEspionageView();
          } else if (curPage === "colonies") {
            refreshColoniesView();
          } else if (curPage === "attacks") {
            refreshAttacksView();
          } else if (curPage === "recyclers") {
            refreshRecyclersView();
          }
          if (!sharedLegacyState.configDirty && sharedLegacyState.configSaving === 0) {
            sharedLegacyState.configDirty = false;
            setConfigDirtyState(false);
            setConfigHydrationTick((t) => t + 1);
          }
          if (!sharedLegacyState.agSimDirty && sharedLegacyState.agSimRunning === 0 && sharedLegacyState.configSaving === 0) {
            const ag = nextDash.action_graph || buildClientFallbackActionGraph(nextDash);
            setActionGraphData(ag);
            setAgHydrationTick((t) => t + 1);
          }
          return true;
        } catch (err) {
          if (reqSeq !== sharedLegacyState.dashboardSeq) return false;
          if (isRateLimitError(err)) {
            setApiStatus(true);
            showFeedback(`SpaceInvasion rate-limit cooldown active: ${err.message}`, "warn");
          } else {
            const msg = (err == null ? void 0 : err.name) === "AbortError" ? "timeout" : (err == null ? void 0 : err.message) || "unreachable";
            setApiStatus(false, msg);
          }
          return false;
        } finally {
          sharedLegacyState.inFlightDashboardCount = Math.max(
            0,
            sharedLegacyState.inFlightDashboardCount - 1
          );
          sharedLegacyState.inFlightDashboard = sharedLegacyState.inFlightDashboardCount > 0;
        }
      },
      [
        applyVersionSnapshot,
        refreshAttacksView,
        refreshColoniesView,
        refreshEspionageView,
        refreshRecyclersView,
        setActionGraphData,
        setApiStatus,
        setBuildingsEtaData,
        setCombatData,
        setLastOptimizerSummary,
        showFeedback,
        syncNotificationsPayload
      ]
    );
    const checkApiHealthAndHotReload = reactExports.useCallback(async () => {
      if (sharedLegacyState.inFlightVersionCheck) return;
      sharedLegacyState.inFlightVersionCheck = true;
      try {
        const vData = await apiGet("/ui-version", 2500);
        if (applyVersionSnapshot(vData)) return;
        const wasOffline = !sharedLegacyState.apiOnline;
        setApiStatus(true);
        if (wasOffline) {
          await loadDashboard({ refresh: false, preserveConfig: true });
        }
      } catch (err) {
        const msg = (err == null ? void 0 : err.name) === "AbortError" ? "timeout" : (err == null ? void 0 : err.message) || "unreachable";
        setApiStatus(false, msg);
      } finally {
        sharedLegacyState.inFlightVersionCheck = false;
      }
    }, [applyVersionSnapshot, loadDashboard, setApiStatus]);
    const setActivePage = reactExports.useCallback(
      (rawKey) => {
        var _a2;
        const normKey = rawKey === "graph" ? "action-graph" : rawKey;
        const target = PAGE_TITLES[normKey] ? normKey : "dashboard";
        sharedLegacyState.activePage = target;
        setActivePageState(target);
        setUiPrefs((prev) => {
          const next = prev.sidebarOpen ? { ...prev, sidebarOpen: false } : prev;
          persistUiPrefs(target, sharedLegacyState.selectedPlanetId, next);
          return next;
        });
        if (typeof window !== "undefined" && window.location.hash !== `#${target}`) {
          window.history.replaceState(null, "", `#${target}`);
        }
        if (target === "buildings") {
          refreshBuildingsEtaForSelectedPlanet();
        } else if (target === "combat") {
          refreshCombatForSelectedPlanet({ refresh: false });
        } else if (target === "espionage") {
          refreshEspionageView();
        } else if (target === "colonies") {
          refreshColoniesView();
        } else if (target === "attacks") {
          refreshAttacksView();
        } else if (target === "recyclers") {
          refreshRecyclersView();
        } else if (target === "action-graph") {
          if (!sharedLegacyState.actionGraphData) {
            if ((_a2 = sharedLegacyState.dashboard) == null ? void 0 : _a2.action_graph) {
              setActionGraphData(sharedLegacyState.dashboard.action_graph);
            } else if (sharedLegacyState.dashboard) {
              setActionGraphData(buildClientFallbackActionGraph(sharedLegacyState.dashboard));
            }
          }
        }
      },
      [
        persistUiPrefs,
        refreshAttacksView,
        refreshBuildingsEtaForSelectedPlanet,
        refreshColoniesView,
        refreshCombatForSelectedPlanet,
        refreshEspionageView,
        refreshRecyclersView,
        setActionGraphData
      ]
    );
    const setSelectedPlanetId = reactExports.useCallback(
      (planetId) => {
        const pid = planetId == null ? null : safeFiniteNumber(planetId, null);
        sharedLegacyState.selectedPlanetId = pid;
        sharedLegacyState.simPopulatedOnce = false;
        setSelectedPlanetIdState(pid);
        persistUiPrefs(sharedLegacyState.activePage, pid, uiPrefsRef.current);
        if (sharedLegacyState.dashboard) {
          refreshBuildingsEtaForSelectedPlanet();
          if (sharedLegacyState.activePage === "combat") {
            refreshCombatForSelectedPlanet({ refresh: false });
          } else if (sharedLegacyState.activePage === "colonies") {
            refreshColoniesView();
          }
        }
      },
      [
        persistUiPrefs,
        refreshBuildingsEtaForSelectedPlanet,
        refreshColoniesView,
        refreshCombatForSelectedPlanet
      ]
    );
    const setUiPref = reactExports.useCallback(
      (key, value2) => {
        if (key in sharedLegacyState) {
          sharedLegacyState[key] = value2;
        }
        setUiPrefs((prev) => {
          const next = { ...prev, [key]: value2 };
          persistUiPrefs(sharedLegacyState.activePage, sharedLegacyState.selectedPlanetId, next);
          return next;
        });
      },
      [persistUiPrefs]
    );
    const refreshNotificationCenter = reactExports.useCallback(
      async (filterOverride) => {
        const statusFilter = filterOverride ?? sharedLegacyState.notifFilter ?? "active";
        try {
          const res = await apiCommand({
            command: "notifications_list",
            status: statusFilter,
            limit: 60
          });
          if (Array.isArray(res == null ? void 0 : res.items)) {
            setNotifications({
              unread_count: Math.max(0, Math.round(safeFiniteNumber(res.unread_count, 0) ?? 0)),
              items: res.items
            });
            sharedLegacyState.notifications = {
              unread_count: Math.max(0, Math.round(safeFiniteNumber(res.unread_count, 0) ?? 0)),
              items: res.items
            };
          }
        } catch {
        }
      },
      []
    );
    const openNotificationCenter = reactExports.useCallback(async () => {
      setNotifCenterOpen(true);
      notifCenterOpenRef.current = true;
      await refreshNotificationCenter();
    }, [refreshNotificationCenter]);
    const closeNotificationCenter = reactExports.useCallback(() => {
      setNotifCenterOpen(false);
      notifCenterOpenRef.current = false;
    }, []);
    const closeHelpPopupOnly = reactExports.useCallback(() => {
      sharedLegacyState.activeHelpNotifId = null;
      setActiveHelpNotif(null);
    }, []);
    const ackActiveHelpPopup = reactExports.useCallback(
      async (targetStatus) => {
        const nid = sharedLegacyState.activeHelpNotifId;
        closeHelpPopupOnly();
        if (!nid || nid <= 0) return;
        try {
          await apiCommand({
            command: "notification_ack",
            notification_id: nid,
            status: targetStatus
          });
        } catch (err) {
          showFeedback(`Failed updating notification: ${err.message}`, feedbackKindForError(err));
        }
      },
      [closeHelpPopupOnly, showFeedback]
    );
    const toggleBlastFurnaceRate = reactExports.useCallback(
      async (targetPct) => {
        const planet = selectActivePlanet(
          sharedLegacyState.dashboard,
          sharedLegacyState.selectedPlanetId
        );
        if (!planet) return;
        try {
          showFeedback(`Setting Blast furnace to ${targetPct}% on [${planet.coords}]...`, "ok");
          const res = await apiCommand({
            command: "throttle_building",
            building_id: 2,
            percentage: Number(targetPct),
            planet_id: planet.planet_id,
            dry_run: false
          });
          showFeedback(res.message || `Blast furnace set to ${targetPct}%.`, "ok");
          await loadDashboard({ refresh: false, force: true });
          await refreshBuildingsEtaForSelectedPlanet(false);
        } catch (err) {
          showFeedback(`Throttle failed: ${err.message}`, feedbackKindForError(err));
        }
      },
      [loadDashboard, refreshBuildingsEtaForSelectedPlanet, showFeedback]
    );
    const syncAllInboxFolders = reactExports.useCallback(async () => {
      try {
        showFeedback(
          "Syncing all in-game message folders (Espionage, Battle, Fleet, User, Alliance, System)...",
          "ok"
        );
        const res = await apiCommand({ command: "sync_messages" });
        const fc = res.folder_counts || {};
        const folderSummary = Object.entries(fc).map(([k, v]) => `${k}:${safeFiniteNumber(v == null ? void 0 : v.total, 0) ?? 0}`).join(" · ");
        const archived = Math.round(safeFiniteNumber(res.archived_messages, 0) ?? 0);
        const spyCnt = Math.round(safeFiniteNumber(res.synced_reports, 0) ?? 0);
        const delNote = res.deleted_in_game ? "deleted in-game" : "stored in SQLite";
        showFeedback(
          `Inbox sync complete: ${archived} archived (${spyCnt} spy) — ${delNote}${folderSummary ? ` [${folderSummary}]` : ""}.`,
          "ok"
        );
        await loadDashboard({ refresh: false, force: true });
        const curPage = sharedLegacyState.activePage;
        if (curPage === "espionage") await refreshEspionageView();
        else if (curPage === "colonies") await refreshColoniesView();
        else if (curPage === "attacks") await refreshAttacksView();
        else if (curPage === "recyclers") await refreshRecyclersView();
      } catch (err) {
        showFeedback(`Inbox sync failed: ${err.message}`, feedbackKindForError(err));
      }
    }, [
      loadDashboard,
      refreshAttacksView,
      refreshColoniesView,
      refreshEspionageView,
      refreshRecyclersView,
      showFeedback
    ]);
    const enqueueManualCommand = reactExports.useCallback(
      async (payload) => {
        try {
          const res = await apiCommand({
            command: "queue_add",
            ...payload
          });
          showFeedback(res.message || "Command pinned to priority queue.", "ok", {
            autoHideMs: 4200
          });
          await loadDashboard({ refresh: false, force: true });
          return res;
        } catch (err) {
          showFeedback(`Queue add failed: ${err.message}`, feedbackKindForError(err));
          return null;
        }
      },
      [loadDashboard, showFeedback]
    );
    useClientSyncEffect$2(() => {
      const g = globalThis;
      g.showFeedback = (msg, kind = "ok", opts) => {
        reactDomExports.flushSync(() => showFeedback(msg, kind, opts));
      };
      g.setActivePage = (page) => {
        reactDomExports.flushSync(() => setActivePage(page));
      };
      g.beginGaCalc = () => {
        reactDomExports.flushSync(() => beginGaCalc());
      };
      g.endGaCalc = () => {
        reactDomExports.flushSync(() => endGaCalc());
      };
      g.loadDashboard = loadDashboard;
      const applySyntheticDash = (partialDash) => {
        reactDomExports.flushSync(() => {
          setDashboard((prev) => {
            const merged = { ...prev || {}, ...partialDash || {} };
            sharedLegacyState.dashboard = merged;
            return merged;
          });
          if (partialDash == null ? void 0 : partialDash.buildings_eta) {
            setBuildingsEtaData(partialDash.buildings_eta);
          }
          if (partialDash == null ? void 0 : partialDash.optimizer_summary) {
            setLastOptimizerSummary(partialDash.optimizer_summary);
          }
        });
      };
      g.renderTelemetry = applySyntheticDash;
      g.renderBuildingsView = applySyntheticDash;
      g.renderGaNnHud = applySyntheticDash;
      g.renderRanking = applySyntheticDash;
    }, [
      loadDashboard,
      setActivePage,
      setBuildingsEtaData,
      setLastOptimizerSummary,
      showFeedback
    ]);
    useClientSyncEffect$2(() => {
      if (disableInitialFetch || typeof window === "undefined") return;
      checkApiHealthAndHotReload();
      loadDashboard({ refresh: false, preserveConfig: false });
      const tickTimer = setInterval(() => {
        if (document.visibilityState === "visible") {
          setNowEpochSec(Math.floor(Date.now() / 1e3));
        }
      }, 1e3);
      const versionTimer = setInterval(() => {
        var _a2, _b;
        if (!((_b = (_a2 = sharedLegacyState.dashboard) == null ? void 0 : _a2.bot_control) == null ? void 0 : _b.captcha_blocked)) {
          checkApiHealthAndHotReload();
        }
      }, 1200);
      const dashTimer = setInterval(() => {
        var _a2, _b;
        if (document.visibilityState === "visible" && sharedLegacyState.apiOnline && !((_b = (_a2 = sharedLegacyState.dashboard) == null ? void 0 : _a2.bot_control) == null ? void 0 : _b.captcha_blocked)) {
          loadDashboard({ refresh: false, preserveConfig: true });
        }
      }, 5e3);
      const onOnline = () => {
        var _a2, _b;
        if (!((_b = (_a2 = sharedLegacyState.dashboard) == null ? void 0 : _a2.bot_control) == null ? void 0 : _b.captcha_blocked)) {
          checkApiHealthAndHotReload();
        }
      };
      const onVis = () => {
        var _a2, _b;
        if (document.visibilityState === "visible" && !((_b = (_a2 = sharedLegacyState.dashboard) == null ? void 0 : _a2.bot_control) == null ? void 0 : _b.captcha_blocked)) {
          checkApiHealthAndHotReload();
          loadDashboard({ refresh: false, preserveConfig: true });
        }
      };
      const onKeyDown = (ev) => {
        if (ev && ev.key === "Escape") {
          if (notifCenterOpenRef.current) {
            closeNotificationCenter();
          } else if (sharedLegacyState.activeHelpNotifId !== null) {
            closeHelpPopupOnly();
          }
        }
      };
      window.addEventListener("online", onOnline);
      document.addEventListener("visibilitychange", onVis);
      document.addEventListener("keydown", onKeyDown);
      return () => {
        clearInterval(tickTimer);
        clearInterval(versionTimer);
        clearInterval(dashTimer);
        window.removeEventListener("online", onOnline);
        document.removeEventListener("visibilitychange", onVis);
        document.removeEventListener("keydown", onKeyDown);
      };
    }, [
      checkApiHealthAndHotReload,
      closeHelpPopupOnly,
      closeNotificationCenter,
      disableInitialFetch,
      loadDashboard
    ]);
    const activePlanet = reactExports.useMemo(
      () => selectActivePlanet(dashboard, selectedPlanetId),
      [dashboard, selectedPlanetId]
    );
    const chromeOnline = Boolean((_a = dashboard == null ? void 0 : dashboard.health) == null ? void 0 : _a.in_game);
    const gaCalculating = gaCalcCount > 0;
    const value = reactExports.useMemo(
      () => ({
        dashboard,
        setDashboard,
        apiOnline,
        reconnectAttempts,
        chromeOnline,
        activePage,
        setActivePage,
        selectedPlanetId,
        setSelectedPlanetId,
        activePlanet,
        nowEpochSec,
        gaCalculating,
        gaCalcCount,
        beginGaCalc,
        endGaCalc,
        feedback,
        showFeedback,
        apiGet,
        apiCommand,
        loadDashboard,
        bumpDashboardSeq,
        uiPrefs,
        setUiPref,
        buildingsEtaData,
        setBuildingsEtaData,
        refreshBuildingsEtaForSelectedPlanet,
        combatData,
        setCombatData,
        refreshCombatForSelectedPlanet,
        espData,
        setEspData,
        refreshEspionageView,
        colonyData,
        setColonyData,
        refreshColoniesView,
        atkData,
        setAtkData,
        refreshAttacksView,
        recData,
        setRecData,
        refreshRecyclersView,
        actionGraphData,
        setActionGraphData,
        agSimDirty,
        setAgSimDirty,
        agSimRunning,
        setAgSimRunning,
        configDirty,
        setConfigDirty,
        configSaving,
        setConfigSaving,
        configHydrationTick,
        agHydrationTick,
        lastOptimizerSummary,
        setLastOptimizerSummary,
        notifications,
        syncNotificationsPayload,
        notifCenterOpen,
        openNotificationCenter,
        closeNotificationCenter,
        refreshNotificationCenter,
        activeHelpNotif,
        ackActiveHelpPopup,
        closeHelpPopupOnly,
        toggleBlastFurnaceRate,
        syncAllInboxFolders,
        enqueueManualCommand
      }),
      [
        ackActiveHelpPopup,
        actionGraphData,
        activeHelpNotif,
        activePage,
        activePlanet,
        agHydrationTick,
        agSimDirty,
        agSimRunning,
        apiOnline,
        atkData,
        beginGaCalc,
        buildingsEtaData,
        bumpDashboardSeq,
        chromeOnline,
        closeHelpPopupOnly,
        closeNotificationCenter,
        colonyData,
        combatData,
        configDirty,
        configHydrationTick,
        configSaving,
        dashboard,
        endGaCalc,
        enqueueManualCommand,
        espData,
        feedback,
        gaCalcCount,
        gaCalculating,
        lastOptimizerSummary,
        loadDashboard,
        notifCenterOpen,
        notifications,
        nowEpochSec,
        openNotificationCenter,
        recData,
        reconnectAttempts,
        refreshAttacksView,
        refreshBuildingsEtaForSelectedPlanet,
        refreshColoniesView,
        refreshCombatForSelectedPlanet,
        refreshEspionageView,
        refreshNotificationCenter,
        refreshRecyclersView,
        selectedPlanetId,
        setActionGraphData,
        setActivePage,
        setAgSimDirty,
        setAtkData,
        setBuildingsEtaData,
        setColonyData,
        setCombatData,
        setConfigDirty,
        setEspData,
        setLastOptimizerSummary,
        setRecData,
        setSelectedPlanetId,
        setUiPref,
        showFeedback,
        syncAllInboxFolders,
        syncNotificationsPayload,
        toggleBlastFurnaceRate,
        uiPrefs
      ]
    );
    return /* @__PURE__ */ jsxRuntimeExports.jsx(ConsoleContext.Provider, { value, children });
  };
  function useConsole() {
    const ctx = reactExports.useContext(ConsoleContext);
    if (!ctx) {
      throw new Error("useConsole must be used inside <ConsoleProvider>");
    }
    return ctx;
  }
  function fmtClock(tsSec) {
    if (!tsSec) return "SYNC --:--:--";
    const ts = safeFiniteNumber(tsSec, null);
    if (ts === null) return "SYNC ACTIVE";
    const d = new Date(ts * 1e3);
    if (Number.isNaN(d.getTime())) return "SYNC ACTIVE";
    return `SYNC ${d.toTimeString().slice(0, 8)}`;
  }
  function computeSelectedBlastFurnacePct(dash, activePlanet, buildingsEtaData) {
    var _a, _b, _c;
    const eta = buildingsEtaData && activePlanet && ((_a = buildingsEtaData.planet) == null ? void 0 : _a.planet_id) === activePlanet.planet_id ? buildingsEtaData : (dash == null ? void 0 : dash.buildings_eta) || null;
    const r2 = safeFiniteNumber((_b = activePlanet == null ? void 0 : activePlanet.economy_rates) == null ? void 0 : _b.resource2, null);
    const etaPct = safeFiniteNumber((_c = eta == null ? void 0 : eta.planet_economy) == null ? void 0 : _c.blast_furnace_pct, null);
    const pct = r2 !== null ? r2 : etaPct !== null ? etaPct : 100;
    return Math.max(0, Math.min(100, Math.round(pct)));
  }
  const AppHeader = () => {
    var _a, _b;
    const {
      dashboard,
      activePage,
      setActivePage,
      activePlanet,
      buildingsEtaData,
      notifications,
      notifCenterOpen,
      openNotificationCenter,
      activeHelpNotif,
      ackActiveHelpPopup,
      apiCommand: apiCommand2,
      loadDashboard,
      showFeedback
    } = useConsole();
    const gs = (dashboard == null ? void 0 : dashboard.state) || {};
    const universeText = dashboard ? `U${gs.universe_id || 31} · ${gs.nickname || "COMMANDER"}` : "U31 · COMMANDER";
    const clockText = fmtClock(gs.timestamp);
    const tempVal = safeFiniteNumber(activePlanet == null ? void 0 : activePlanet.temperature, null);
    const fUsedVal = safeFiniteNumber(activePlanet == null ? void 0 : activePlanet.fields_used, null);
    const fMaxVal = safeFiniteNumber(activePlanet == null ? void 0 : activePlanet.fields_max, null);
    const specsText = activePlanet ? `TEMP ${tempVal === null ? "—" : tempVal}°C · FIELDS ${fUsedVal === null ? "—" : fUsedVal}/${fMaxVal === null ? "—" : fMaxVal}` : "TEMP --°C · FIELDS --/--";
    const b2Pct = computeSelectedBlastFurnacePct(dashboard, activePlanet, buildingsEtaData);
    const bfText = b2Pct === 0 ? "B2: 0% PAUSED" : `B2: ${b2Pct}%`;
    const galSweep = (dashboard == null ? void 0 : dashboard.galaxy_sweep) || null;
    const galTotal = Math.max(0, Math.round(safeFiniteNumber(galSweep == null ? void 0 : galSweep.total_systems, 0) ?? 0));
    const galExplored = Math.max(
      0,
      Math.round(safeFiniteNumber(galSweep == null ? void 0 : galSweep.explored_systems, 0) ?? 0)
    );
    const galCovRaw = safeFiniteNumber(galSweep == null ? void 0 : galSweep.coverage_pct, null);
    const galCovPct = galCovRaw !== null ? Math.max(0, Math.min(100, galCovRaw)) : galTotal > 0 ? Math.max(0, Math.min(100, galExplored / galTotal * 100)) : null;
    const galPhaseLabel = String((galSweep == null ? void 0 : galSweep.scan_phase) || "").toLowerCase() === "targeted_refresh" ? "Targeted Refresh" : "Exploratory";
    const galList = Array.isArray(galSweep == null ? void 0 : galSweep.scan_galaxies) && galSweep.scan_galaxies.length > 0 ? galSweep.scan_galaxies.map((g) => `G${g}`).join(",") : "Galaxy";
    const galCovText = galCovPct !== null ? `GAL ${galCovPct.toFixed(1)}%` : "GAL --%";
    const galCovTitle = galCovPct !== null ? `${galList} explored: ${galExplored}/${galTotal} systems (${galCovPct.toFixed(1)}%) · Phase: ${galPhaseLabel}${(galSweep == null ? void 0 : galSweep.in_progress) ? " · Sweeping..." : ""}` : "Galaxy exploration progress — click to open Espionage & Galaxy Scanner";
    const hostileCount = Array.isArray(gs.fleet_alerts) ? gs.fleet_alerts.filter((a) => a == null ? void 0 : a.hostile).length : 0;
    const unreadCnt = notifications.unread_count || 0;
    const captchaBlocked = Boolean((_a = dashboard == null ? void 0 : dashboard.bot_control) == null ? void 0 : _a.captcha_blocked);
    const captchaMsg = ((_b = dashboard == null ? void 0 : dashboard.bot_control) == null ? void 0 : _b.captcha_message) || "CAPTCHA / Security verification active in Chrome (:9222). Automation is paused and waiting passively — solve it manually in Chrome, then click Resume.";
    const helpModalHasCaptchaBtn = Boolean(
      activeHelpNotif && (activeHelpNotif.category === "captcha_gate" || activeHelpNotif.category === "antibot")
    );
    const onResumeFromCaptcha = async () => {
      try {
        await apiCommand2({ command: "bot_control", paused: false, captcha_solved: true });
        if (activeHelpNotif) {
          await ackActiveHelpPopup("read");
        }
        await loadDashboard({ refresh: true, force: true });
        showFeedback("CAPTCHA solved confirmed — page refresh & Commander automation resumed.", "ok");
      } catch (err) {
        showFeedback(`Resume failed: ${err.message}`, feedbackKindForError(err));
      }
    };
    return /* @__PURE__ */ jsxRuntimeExports.jsxs(jsxRuntimeExports.Fragment, { children: [
      /* @__PURE__ */ jsxRuntimeExports.jsxs("header", { className: "hud-ruler", "aria-label": "Session telemetry bar", children: [
        /* @__PURE__ */ jsxRuntimeExports.jsxs("div", { className: "ruler-cluster", children: [
          /* @__PURE__ */ jsxRuntimeExports.jsx("span", { className: "ruler-page-tag tabular", id: "hud-page-title", children: PAGE_TITLES[activePage] || "DASHBOARD" }),
          /* @__PURE__ */ jsxRuntimeExports.jsx("span", { className: "ruler-sep", "aria-hidden": "true" }),
          /* @__PURE__ */ jsxRuntimeExports.jsx("span", { className: "ruler-node tabular", id: "hud-universe", children: universeText }),
          /* @__PURE__ */ jsxRuntimeExports.jsx("span", { className: "ruler-sep", "aria-hidden": "true" }),
          /* @__PURE__ */ jsxRuntimeExports.jsx("span", { className: "ruler-node tabular", id: "hud-clock", children: clockText }),
          /* @__PURE__ */ jsxRuntimeExports.jsx("span", { className: "ruler-sep", "aria-hidden": "true" }),
          /* @__PURE__ */ jsxRuntimeExports.jsx("span", { className: "ruler-node tabular", id: "planet-specs", children: specsText })
        ] }),
        /* @__PURE__ */ jsxRuntimeExports.jsxs("div", { className: "ruler-cluster", children: [
          /* @__PURE__ */ jsxRuntimeExports.jsx(
            "button",
            {
              type: "button",
              className: `gal-cov-pill tabular${galCovPct !== null && galCovPct >= 100 ? " is-complete" : ""}`,
              id: "hud-galaxy-coverage",
              title: galCovTitle,
              onClick: () => setActivePage("espionage"),
              children: galCovText
            }
          ),
          /* @__PURE__ */ jsxRuntimeExports.jsx(
            "button",
            {
              type: "button",
              className: `bf-pill tabular${b2Pct === 0 ? " is-paused" : ""}`,
              id: "hud-bf-pill",
              title: "Blast furnace rate on selected planet — click to open Buildings & ETAs",
              onClick: () => setActivePage("buildings"),
              children: bfText
            }
          ),
          /* @__PURE__ */ jsxRuntimeExports.jsxs(
            "button",
            {
              type: "button",
              className: `notif-bell-btn tabular${unreadCnt > 0 ? " has-unread" : ""}`,
              id: "btn-notifications",
              "aria-label": unreadCnt > 0 ? `Open system notifications (${unreadCnt} unread)` : "Open system notifications",
              "aria-haspopup": "dialog",
              "aria-expanded": notifCenterOpen ? "true" : "false",
              title: "System notifications and validation alerts",
              onClick: () => openNotificationCenter(),
              children: [
                /* @__PURE__ */ jsxRuntimeExports.jsx("img", { src: "/bell.svg", alt: "", width: 15, height: 15, className: "notif-bell-img" }),
                /* @__PURE__ */ jsxRuntimeExports.jsx("span", { className: "notif-count-badge", id: "notif-unread-badge", hidden: unreadCnt <= 0, children: unreadCnt > 99 ? "99+" : String(unreadCnt) })
              ]
            }
          ),
          /* @__PURE__ */ jsxRuntimeExports.jsxs(
            "div",
            {
              className: `beacon-pill tabular${hostileCount > 0 || captchaBlocked ? " is-alert" : ""}`,
              id: "hud-alert-beacon",
              role: "status",
              children: [
                /* @__PURE__ */ jsxRuntimeExports.jsx("span", { className: "beacon-mark", "aria-hidden": "true" }),
                /* @__PURE__ */ jsxRuntimeExports.jsx("span", { id: "val-alerts", children: captchaBlocked ? "CAPTCHA PAUSE · SOLVE IN CHROME" : hostileCount > 0 ? `HOSTILE ALERT (${hostileCount})` : "SECTOR CLEAR · 0 HOSTILES" })
              ]
            }
          )
        ] })
      ] }),
      /* @__PURE__ */ jsxRuntimeExports.jsxs(
        "div",
        {
          className: "captcha-gate-banner",
          id: "captcha-gate-banner",
          role: "alert",
          hidden: !captchaBlocked,
          children: [
            /* @__PURE__ */ jsxRuntimeExports.jsxs("div", { className: "captcha-gate-copy", children: [
              /* @__PURE__ */ jsxRuntimeExports.jsx("span", { className: "module-code tabular", children: "GATE" }),
              /* @__PURE__ */ jsxRuntimeExports.jsx("span", { children: captchaMsg })
            ] }),
            /* @__PURE__ */ jsxRuntimeExports.jsx(
              "button",
              {
                type: "button",
                className: "btn btn-primary btn-sm",
                id: helpModalHasCaptchaBtn ? "btn-header-captcha-resume" : "btn-captcha-resume",
                onClick: onResumeFromCaptcha,
                children: "Solved in Chrome — Resume Bot"
              }
            )
          ]
        }
      )
    ] });
  };
  const AppSidebar = () => {
    var _a, _b, _c, _d, _e, _f, _g, _h;
    const {
      dashboard,
      apiOnline,
      reconnectAttempts,
      activePage,
      setActivePage,
      activePlanet,
      setSelectedPlanetId,
      uiPrefs,
      setUiPref,
      espData,
      colonyData,
      atkData,
      recData,
      beginGaCalc,
      endGaCalc,
      loadDashboard,
      apiCommand: apiCommand2,
      showFeedback
    } = useConsole();
    const health = (dashboard == null ? void 0 : dashboard.health) || {};
    const gs = (dashboard == null ? void 0 : dashboard.state) || {};
    const planets = Array.isArray(gs.planets) ? gs.planets : [];
    const port = health.debug_port ? "8787" : typeof window !== "undefined" && window.location.port || "8787";
    const apiPipClass = apiOnline ? "status-pip online" : "status-pip offline";
    const apiStatusText = apiOnline ? `API :${port} ONLINE · HOT` : reconnectAttempts > 0 ? `API :${port} RECONNECTING (#${reconnectAttempts})` : "API :8787 CONNECTING...";
    const cdSec = Math.ceil(safeFiniteNumber(health.retry_after, 0) ?? 0);
    const cdTag = health.rate_limited && cdSec > 0 ? ` · COOLDOWN ${cdSec}s` : health.rate_limited ? " · COOLDOWN" : "";
    const chromePipClass = health.in_game ? "status-pip online" : health.debug_port_open ? "status-pip" : "status-pip offline";
    const chromeStatusText = dashboard ? health.in_game ? `CHROME :${health.debug_port || 9222} ONLINE${cdTag}` : health.debug_port_open ? `CHROME :${health.debug_port || 9222} STANDBY` : `CHROME :${health.debug_port || 9222} OFFLINE` : "Connecting Chrome :9222...";
    const showEnsureBtn = Boolean(dashboard && !health.in_game);
    const spdVal = safeFiniteNumber(gs.speedup_factor, null);
    const speedupText = spdVal !== null && spdVal > 0 ? `SPEED x${spdVal}` : dashboard ? "STD x1" : "SPEED x1";
    const botCtrl = (dashboard == null ? void 0 : dashboard.bot_control) || {};
    const paused = Boolean(botCtrl.paused);
    const modeVal = uiPrefs.modeOverride || botCtrl.mode_override || "auto";
    const runBadgeText = paused ? "PAUSED" : modeVal === "dry-run" ? "DRY" : "RUN";
    const runBadgeCls = paused ? "run-badge tabular is-paused" : modeVal === "dry-run" ? "run-badge tabular is-dry" : "run-badge tabular";
    const batchIv = safeFiniteNumber(
      health.action_batch_interval_seconds ?? ((_a = dashboard == null ? void 0 : dashboard.config) == null ? void 0 : _a.action_batch_interval_seconds),
      null
    );
    const budgetText = dashboard ? batchIv !== null && batchIv > 0 ? `BATCH ${Math.round(batchIv)}s` : `${health.remaining_hourly_actions ?? "—"} OPS/HR` : "-- OPS/HR";
    const cfgMode = String(
      ((_b = dashboard == null ? void 0 : dashboard.config) == null ? void 0 : _b.planner_mode) || (dashboard == null ? void 0 : dashboard.planner_mode) || "priority"
    ).toLowerCase();
    const isGaNn = cfgMode === "ga_nn";
    const counts = ((_c = dashboard == null ? void 0 : dashboard.db_summary) == null ? void 0 : _c.counts) || {};
    const spyCnt = Math.max(
      0,
      Math.round(
        safeFiniteNumber(
          ((_d = espData == null ? void 0 : espData.summary) == null ? void 0 : _d.spied_targets) ?? counts.spy_reports ?? counts.spy_targets ?? 0,
          0
        ) ?? 0
      )
    );
    const colCnt = Math.max(
      0,
      Math.round(
        safeFiniteNumber(
          colonyData ? colonyData.qualified_count ?? colonyData.probed_count ?? 0 : counts.galaxy_slots ?? 0,
          0
        ) ?? 0
      )
    );
    const atkCnt = Math.max(
      0,
      Math.round(
        safeFiniteNumber(((_e = atkData == null ? void 0 : atkData.summary) == null ? void 0 : _e.total_battles) ?? counts.battle_reports ?? 0, 0) ?? 0
      )
    );
    const recCnt = Math.max(
      0,
      Math.round(
        safeFiniteNumber(((_f = recData == null ? void 0 : recData.summary) == null ? void 0 : _f.total_runs) ?? counts.recycle_reports ?? 0, 0) ?? 0
      )
    );
    const planCount = Array.isArray(dashboard == null ? void 0 : dashboard.plan) ? dashboard.plan.length : 0;
    const rnkCur = ((_g = dashboard == null ? void 0 : dashboard.ranking) == null ? void 0 : _g.current) || {};
    const srvRank = safeFiniteNumber(rnkCur.server_rank, null);
    const rankBadgeTxt = rnkCur.server_rank != null ? srvRank !== null ? `#${srvRank}` : "#—" : ((_h = rnkCur.attack_zone) == null ? void 0 : _h.label) || "#—";
    const onSync = async () => {
      var _a2, _b2, _c2, _d2;
      if ((_a2 = dashboard == null ? void 0 : dashboard.bot_control) == null ? void 0 : _a2.captcha_blocked) {
        showFeedback(
          "Page refresh is frozen while CAPTCHA is active — solve it in Chrome (:9222) and click 'Solved in Chrome — Resume Bot'.",
          "warn"
        );
        return;
      }
      const useGa = String(((_b2 = dashboard == null ? void 0 : dashboard.config) == null ? void 0 : _b2.planner_mode) || "").toLowerCase() === "ga_nn";
      if (useGa) beginGaCalc();
      try {
        showFeedback("Syncing live snapshot from Chrome...", "ok");
        const ok = await loadDashboard({ refresh: true, preserveConfig: true, force: true });
        if (ok) {
          const latestDash = ((_c2 = globalThis.state) == null ? void 0 : _c2.dashboard) || dashboard;
          if ((_d2 = latestDash == null ? void 0 : latestDash.health) == null ? void 0 : _d2.rate_limited) {
            const rem = Math.ceil(safeFiniteNumber(latestDash.health.retry_after, 0) ?? 0);
            showFeedback(
              `Synced cached state — SpaceInvasion rate-limit cooldown active (${rem > 0 ? `${rem}s` : "wait"}).`,
              "warn"
            );
          } else {
            showFeedback("Synced with live Chrome session.", "ok");
          }
        }
      } finally {
        if (useGa) endGaCalc();
      }
    };
    const onEnsureSession = async () => {
      var _a2;
      if ((_a2 = dashboard == null ? void 0 : dashboard.bot_control) == null ? void 0 : _a2.captcha_blocked) {
        showFeedback(
          "Browser attachment is frozen while CAPTCHA is active — solve it in Chrome (:9222) and click 'Solved in Chrome — Resume Bot'.",
          "warn"
        );
        return;
      }
      try {
        showFeedback("Attaching Chrome to SpaceInvasion universe...", "ok");
        const res = await apiCommand2({ command: "ensure_session" });
        showFeedback(res.message || "Entered universe.", "ok");
        await loadDashboard({ refresh: true, preserveConfig: true, force: true });
      } catch (err) {
        showFeedback(`Enter universe failed: ${err.message}`, feedbackKindForError(err));
      }
    };
    const onPauseToggle = async () => {
      var _a2, _b2;
      const currentPaused = Boolean((_a2 = dashboard == null ? void 0 : dashboard.bot_control) == null ? void 0 : _a2.paused);
      const wasCaptcha = Boolean((_b2 = dashboard == null ? void 0 : dashboard.bot_control) == null ? void 0 : _b2.captcha_blocked);
      try {
        await apiCommand2({
          command: "bot_control",
          paused: !currentPaused,
          ...wasCaptcha && currentPaused ? { captcha_solved: true } : {}
        });
        await loadDashboard({ refresh: Boolean(wasCaptcha && currentPaused), force: true });
        showFeedback(
          !currentPaused ? "Commander auto-loop paused." : "Commander auto-loop resumed.",
          "ok"
        );
      } catch (err) {
        showFeedback(`Pause update failed: ${err.message}`, feedbackKindForError(err));
      }
    };
    const onModeChange = async (mode) => {
      const val = mode === "auto" ? null : mode;
      try {
        await apiCommand2({ command: "bot_control", mode_override: val });
        setUiPref("modeOverride", mode);
        await loadDashboard({ refresh: false, force: true });
        showFeedback(`Loop mode set to ${mode}.`, "ok");
      } catch (err) {
        showFeedback(`Mode update failed: ${err.message}`, feedbackKindForError(err));
      }
    };
    const onTickNow = async () => {
      var _a2;
      const useGa = String(((_a2 = dashboard == null ? void 0 : dashboard.config) == null ? void 0 : _a2.planner_mode) || "").toLowerCase() === "ga_nn";
      if (useGa) beginGaCalc();
      try {
        const dryRun = modeVal === "dry-run";
        showFeedback(`Running 1 ${dryRun ? "dry-run" : "live"} tick...`, "ok");
        const res = await apiCommand2({ command: "tick_once", dry_run: dryRun });
        showFeedback(res.message || "Tick finished.", "ok");
        await loadDashboard({ refresh: false, force: true });
      } catch (err) {
        showFeedback(`Tick failed: ${err.message}`, feedbackKindForError(err));
      } finally {
        if (useGa) endGaCalc();
      }
    };
    const navItems = [
      { page: "dashboard", code: "DSH", title: "Dashboard" },
      { page: "buildings", code: "BLD", title: "Buildings & ETAs", badgeId: "nav-bld-badge", badgeText: "21" },
      { page: "combat", code: "CMB", title: "Combat & Fleet", badgeId: "nav-cmb-badge", badgeText: "1:1" },
      { page: "espionage", code: "SPY", title: "Espionage Intel", badgeId: "nav-spy-badge", badgeText: String(spyCnt) },
      { page: "colonies", code: "COL", title: "Possible Colonies", badgeId: "nav-col-badge", badgeText: String(colCnt) },
      { page: "attacks", code: "ATK", title: "Attack Results", badgeId: "nav-atk-badge", badgeText: String(atkCnt) },
      { page: "recyclers", code: "REC", title: "Recyclers & Inbox", badgeId: "nav-rec-badge", badgeText: String(recCnt) },
      { page: "operations", code: "OPS", title: "Operations Queue", badgeId: "nav-queue-count", badgeText: String(planCount) },
      { page: "action-graph", code: "DAG", title: "Action Graph", badgeId: "nav-graph-badge", badgeText: isGaNn ? "GA" : "PRI", badgeExtraCls: isGaNn ? " is-ga" : "" },
      { page: "ranking", code: "RNK", title: "Ranking Evolution", badgeId: "nav-rank-badge", badgeText: rankBadgeTxt },
      { page: "config", code: "CFG", title: "Strategy Config" },
      { page: "logs", code: "LOG", title: "Live Logs" }
    ];
    return /* @__PURE__ */ jsxRuntimeExports.jsxs(
      "aside",
      {
        className: `hud-sidebar${uiPrefs.sidebarOpen ? " is-open" : ""}`,
        id: "app-sidebar",
        "aria-label": "Primary navigation and autopilot",
        children: [
          /* @__PURE__ */ jsxRuntimeExports.jsxs("div", { className: "sidebar-head", children: [
            /* @__PURE__ */ jsxRuntimeExports.jsxs("div", { className: "sidebar-brand", children: [
              /* @__PURE__ */ jsxRuntimeExports.jsx("span", { className: "module-code tabular", children: "CMD" }),
              /* @__PURE__ */ jsxRuntimeExports.jsxs("div", { className: "brand-titles", children: [
                /* @__PURE__ */ jsxRuntimeExports.jsx("h1", { children: "SpaceInvasion Control" }),
                /* @__PURE__ */ jsxRuntimeExports.jsx("span", { className: "brand-sub tabular", id: "val-speedup", children: speedupText })
              ] })
            ] }),
            /* @__PURE__ */ jsxRuntimeExports.jsx(
              "button",
              {
                type: "button",
                className: "btn btn-secondary btn-sm sidebar-mobile-toggle",
                id: "btn-sidebar-toggle",
                "aria-expanded": uiPrefs.sidebarOpen ? "true" : "false",
                "aria-controls": "sidebar-body",
                onClick: () => setUiPref("sidebarOpen", !uiPrefs.sidebarOpen),
                children: "Menu"
              }
            )
          ] }),
          /* @__PURE__ */ jsxRuntimeExports.jsxs("div", { className: "sidebar-body", id: "sidebar-body", children: [
            /* @__PURE__ */ jsxRuntimeExports.jsx("section", { className: "sidebar-section", "aria-label": "Browser and API session status", children: /* @__PURE__ */ jsxRuntimeExports.jsxs("div", { className: "session-card", children: [
              /* @__PURE__ */ jsxRuntimeExports.jsxs("div", { className: "session-row", children: [
                /* @__PURE__ */ jsxRuntimeExports.jsx("span", { className: apiPipClass, id: "api-pip", "aria-hidden": "true" }),
                /* @__PURE__ */ jsxRuntimeExports.jsx("span", { id: "api-status-text", className: "tabular", children: apiStatusText })
              ] }),
              /* @__PURE__ */ jsxRuntimeExports.jsxs("div", { className: "session-row", children: [
                /* @__PURE__ */ jsxRuntimeExports.jsx("span", { className: chromePipClass, id: "chrome-pip", "aria-hidden": "true" }),
                /* @__PURE__ */ jsxRuntimeExports.jsx("span", { id: "chrome-status-text", className: "tabular", children: chromeStatusText })
              ] }),
              /* @__PURE__ */ jsxRuntimeExports.jsx(
                "button",
                {
                  type: "button",
                  className: "btn btn-warning btn-sm",
                  id: "btn-ensure-session",
                  hidden: !showEnsureBtn,
                  onClick: onEnsureSession,
                  children: "Enter Universe"
                }
              )
            ] }) }),
            /* @__PURE__ */ jsxRuntimeExports.jsxs("nav", { className: "sidebar-section sidebar-nav", "aria-label": "Console views", children: [
              /* @__PURE__ */ jsxRuntimeExports.jsx("div", { className: "nav-label tabular", children: "Navigation" }),
              navItems.map((item) => {
                const isAct = activePage === item.page;
                return /* @__PURE__ */ jsxRuntimeExports.jsxs(
                  "button",
                  {
                    type: "button",
                    className: `sidebar-link${isAct ? " active" : ""}`,
                    "data-page": item.page,
                    "aria-current": isAct ? "page" : void 0,
                    onClick: () => setActivePage(item.page),
                    children: [
                      /* @__PURE__ */ jsxRuntimeExports.jsx("span", { className: "nav-code tabular", children: item.code }),
                      /* @__PURE__ */ jsxRuntimeExports.jsx("span", { className: "nav-title", children: item.title }),
                      item.badgeId ? /* @__PURE__ */ jsxRuntimeExports.jsx(
                        "span",
                        {
                          className: `nav-badge tabular${item.badgeExtraCls || ""}`,
                          id: item.badgeId,
                          children: item.badgeText
                        }
                      ) : null
                    ]
                  },
                  item.page
                );
              })
            ] }),
            /* @__PURE__ */ jsxRuntimeExports.jsx("section", { className: "sidebar-section", "aria-label": "Colony selector", children: /* @__PURE__ */ jsxRuntimeExports.jsxs("div", { className: "control-field", children: [
              /* @__PURE__ */ jsxRuntimeExports.jsx("label", { htmlFor: "planet-select", children: "Active Colony" }),
              /* @__PURE__ */ jsxRuntimeExports.jsx(
                "select",
                {
                  id: "planet-select",
                  "aria-label": "Active planet",
                  value: activePlanet ? String(activePlanet.planet_id) : "",
                  onChange: (e) => setSelectedPlanetId(Number(e.target.value)),
                  children: planets.length === 0 ? /* @__PURE__ */ jsxRuntimeExports.jsx("option", { value: "", children: "Loading..." }) : planets.map((p) => /* @__PURE__ */ jsxRuntimeExports.jsxs("option", { value: String(p.planet_id), children: [
                    p.coords,
                    " ",
                    p.name
                  ] }, p.planet_id))
                }
              )
            ] }) }),
            /* @__PURE__ */ jsxRuntimeExports.jsxs("section", { className: "sidebar-section sidebar-autopilot", "aria-label": "Commander autopilot", children: [
              /* @__PURE__ */ jsxRuntimeExports.jsxs("div", { className: "autopilot-status", children: [
                /* @__PURE__ */ jsxRuntimeExports.jsx("span", { className: runBadgeCls, id: "loop-run-badge", children: runBadgeText }),
                /* @__PURE__ */ jsxRuntimeExports.jsx("span", { className: "budget-readout tabular", id: "val-budget", children: budgetText })
              ] }),
              /* @__PURE__ */ jsxRuntimeExports.jsxs("div", { className: "control-field", children: [
                /* @__PURE__ */ jsxRuntimeExports.jsx("label", { id: "mode-group-label", children: "Execution Mode" }),
                /* @__PURE__ */ jsxRuntimeExports.jsx("div", { className: "mode-stack", role: "radiogroup", "aria-labelledby": "mode-group-label", children: [
                  { key: "auto", label: "CLI Default" },
                  { key: "live", label: "Force Live" },
                  { key: "dry-run", label: "Force Dry-Run" }
                ].map((m) => {
                  const isAct = modeVal === m.key;
                  return /* @__PURE__ */ jsxRuntimeExports.jsx(
                    "button",
                    {
                      type: "button",
                      className: `stack-item${isAct ? " active" : ""}`,
                      role: "radio",
                      "aria-checked": isAct ? "true" : "false",
                      "data-mode": m.key,
                      onClick: () => onModeChange(m.key),
                      children: m.label
                    },
                    m.key
                  );
                }) })
              ] }),
              /* @__PURE__ */ jsxRuntimeExports.jsxs("div", { className: "stack-actions", children: [
                /* @__PURE__ */ jsxRuntimeExports.jsx(
                  "button",
                  {
                    type: "button",
                    className: `btn btn-secondary btn-sm${paused ? " btn-warning" : ""}`,
                    id: "btn-pause-toggle",
                    "aria-pressed": paused ? "true" : "false",
                    onClick: onPauseToggle,
                    children: /* @__PURE__ */ jsxRuntimeExports.jsx("span", { id: "pause-label", children: paused ? "Resume Auto" : "Pause Auto" })
                  }
                ),
                /* @__PURE__ */ jsxRuntimeExports.jsx("button", { type: "button", className: "btn btn-secondary btn-sm", id: "btn-sync", onClick: onSync, children: /* @__PURE__ */ jsxRuntimeExports.jsx("span", { children: "Sync" }) }),
                /* @__PURE__ */ jsxRuntimeExports.jsx(
                  "button",
                  {
                    type: "button",
                    className: "btn btn-primary btn-sm btn-span",
                    id: "btn-tick-now",
                    onClick: onTickNow,
                    children: /* @__PURE__ */ jsxRuntimeExports.jsx("span", { children: "Run 1 Tick" })
                  }
                )
              ] })
            ] }),
            /* @__PURE__ */ jsxRuntimeExports.jsxs("div", { className: "sidebar-foot tabular", children: [
              /* @__PURE__ */ jsxRuntimeExports.jsx("span", { children: "WIN // VIEWS" }),
              /* @__PURE__ */ jsxRuntimeExports.jsx("span", { children: "API :8787" })
            ] })
          ] })
        ]
      }
    );
  };
  const FeedbackBanner = () => {
    const { feedback } = useConsole();
    const isHidden = !feedback || feedback.hidden;
    const cls = (feedback == null ? void 0 : feedback.kind) === "error" ? "is-error" : (feedback == null ? void 0 : feedback.kind) === "warn" ? "is-warn" : "is-ok";
    return /* @__PURE__ */ jsxRuntimeExports.jsx(
      "div",
      {
        id: "feedback-banner",
        className: `feedback-banner${feedback ? ` ${cls}` : ""}`,
        role: "status",
        "aria-live": "polite",
        hidden: isHidden,
        children: (feedback == null ? void 0 : feedback.message) || ""
      }
    );
  };
  function formatNotificationCode(n) {
    const rawCode = typeof (n == null ? void 0 : n.notification_code) === "string" ? n.notification_code.trim() : "";
    if (rawCode) return rawCode;
    const nid = Math.max(0, Math.trunc(safeFiniteNumber(n == null ? void 0 : n.id, 0) ?? 0));
    return nid > 0 ? `NTF-${String(nid).padStart(4, "0")}` : "NTF-0000";
  }
  async function copyNotificationCode(code, showFeedback) {
    var _a;
    const clean = String(code || "").trim();
    if (!clean) return;
    let copied = false;
    try {
      if (typeof navigator !== "undefined" && ((_a = navigator.clipboard) == null ? void 0 : _a.writeText)) {
        await navigator.clipboard.writeText(clean);
        copied = true;
      }
    } catch {
      copied = false;
    }
    if (!copied && typeof document !== "undefined") {
      try {
        const ta = document.createElement("textarea");
        ta.value = clean;
        ta.setAttribute("readonly", "true");
        ta.style.position = "fixed";
        ta.style.opacity = "0";
        document.body.appendChild(ta);
        ta.select();
        if (typeof document.execCommand === "function") {
          copied = Boolean(document.execCommand("copy"));
        }
        document.body.removeChild(ta);
      } catch {
        copied = false;
      }
    }
    showFeedback(
      copied ? `Copied notification ID ${clean} — paste it in chat to investigate.` : `Notification ID: ${clean}`,
      "ok"
    );
  }
  function formatNotifAge$1(tsVal) {
    const ts = safeFiniteNumber(tsVal, 0) ?? 0;
    if (ts <= 0) return "just now";
    const sec = Math.max(0, Math.round(Date.now() / 1e3 - ts));
    if (sec < 60) return `${sec}s ago`;
    if (sec < 3600) return `${Math.floor(sec / 60)}m ago`;
    if (sec < 86400) return `${Math.floor(sec / 3600)}h ago`;
    return `${Math.floor(sec / 86400)}d ago`;
  }
  const HelpAlertModal = () => {
    var _a;
    const {
      dashboard,
      activeHelpNotif,
      ackActiveHelpPopup,
      closeHelpPopupOnly,
      openNotificationCenter,
      apiCommand: apiCommand2,
      loadDashboard,
      showFeedback
    } = useConsole();
    const n = activeHelpNotif;
    const notifCode = formatNotificationCode(n);
    const sev = String((n == null ? void 0 : n.severity) || "ALERT").toUpperCase();
    const title = String((n == null ? void 0 : n.title) || "Action Validation Needs Attention");
    const cnt = Math.max(1, Math.round(safeFiniteNumber(n == null ? void 0 : n.occurrence_count, 1) ?? 1));
    const metaParts = [];
    if (n == null ? void 0 : n.planet_coords) metaParts.push(`[${n.planet_coords}]`);
    if (n == null ? void 0 : n.action_kind) metaParts.push(String(n.action_kind));
    if (cnt > 1) metaParts.push(`x${cnt}`);
    if (n) metaParts.push(formatNotifAge$1(n.updated_ts));
    const metaTxt = n ? metaParts.join(" · ") : "SYSTEM";
    const isCaptchaGate = Boolean(
      n && (n.category === "captcha_gate" || n.category === "antibot" || ((_a = dashboard == null ? void 0 : dashboard.bot_control) == null ? void 0 : _a.captcha_blocked))
    );
    const onCaptchaResume = async () => {
      try {
        await apiCommand2({ command: "bot_control", paused: false, captcha_solved: true });
        await ackActiveHelpPopup("read");
        await loadDashboard({ refresh: true, force: true });
        showFeedback("CAPTCHA solved confirmed — page refresh & Commander automation resumed.", "ok");
      } catch (err) {
        showFeedback(`Resume failed: ${err.message}`, feedbackKindForError(err));
      }
    };
    return /* @__PURE__ */ jsxRuntimeExports.jsx(
      "div",
      {
        className: "notif-modal-backdrop",
        id: "notif-help-modal",
        role: "alertdialog",
        "aria-modal": "true",
        "aria-labelledby": "notif-help-title",
        "aria-describedby": "notif-help-prompt",
        hidden: !n,
        children: /* @__PURE__ */ jsxRuntimeExports.jsxs("div", { className: "notif-modal-card notif-help-card", children: [
          /* @__PURE__ */ jsxRuntimeExports.jsxs("div", { className: "window-bar", children: [
            /* @__PURE__ */ jsxRuntimeExports.jsxs("div", { className: "window-title-group", children: [
              /* @__PURE__ */ jsxRuntimeExports.jsx("span", { className: "module-code tabular", id: "notif-help-sev", children: sev }),
              /* @__PURE__ */ jsxRuntimeExports.jsx(
                "span",
                {
                  className: "notif-id-pill tabular",
                  id: "notif-help-id",
                  title: "Notification ID — copy and share to troubleshoot",
                  children: notifCode
                }
              ),
              /* @__PURE__ */ jsxRuntimeExports.jsx("h2", { id: "notif-help-title", children: title })
            ] }),
            /* @__PURE__ */ jsxRuntimeExports.jsx("span", { className: "module-meta tabular", id: "notif-help-meta", children: metaTxt })
          ] }),
          /* @__PURE__ */ jsxRuntimeExports.jsxs("div", { className: "notif-modal-body", children: [
            /* @__PURE__ */ jsxRuntimeExports.jsx("p", { className: "notif-msg-box tabular", id: "notif-help-message", children: String((n == null ? void 0 : n.message) || "") }),
            /* @__PURE__ */ jsxRuntimeExports.jsx("p", { className: "notif-prompt-copy", id: "notif-help-prompt", children: n ? String(
              n.help_prompt || "Please inspect Chrome (:9222) to verify this action or acknowledge to continue."
            ) : "" })
          ] }),
          /* @__PURE__ */ jsxRuntimeExports.jsxs("div", { className: "notif-modal-footer", children: [
            /* @__PURE__ */ jsxRuntimeExports.jsxs("div", { className: "inline-actions", children: [
              /* @__PURE__ */ jsxRuntimeExports.jsx(
                "button",
                {
                  type: "button",
                  className: "btn btn-secondary btn-sm",
                  id: "btn-notif-help-all",
                  onClick: () => {
                    closeHelpPopupOnly();
                    openNotificationCenter();
                  },
                  children: "All Notifications"
                }
              ),
              /* @__PURE__ */ jsxRuntimeExports.jsxs(
                "button",
                {
                  type: "button",
                  className: "btn btn-secondary btn-sm",
                  id: "btn-notif-help-copy-id",
                  onClick: () => {
                    void copyNotificationCode(notifCode, showFeedback);
                  },
                  children: [
                    "Copy ID (",
                    notifCode,
                    ")"
                  ]
                }
              )
            ] }),
            /* @__PURE__ */ jsxRuntimeExports.jsxs("div", { className: "inline-actions", children: [
              /* @__PURE__ */ jsxRuntimeExports.jsx(
                "button",
                {
                  type: "button",
                  className: "btn btn-secondary btn-sm",
                  id: "btn-notif-help-dismiss",
                  onClick: () => isCaptchaGate ? closeHelpPopupOnly() : ackActiveHelpPopup("dismissed"),
                  children: isCaptchaGate ? "Hide Popup" : "Dismiss"
                }
              ),
              isCaptchaGate ? /* @__PURE__ */ jsxRuntimeExports.jsx(
                "button",
                {
                  type: "button",
                  className: "btn btn-primary btn-sm",
                  id: "btn-captcha-resume",
                  onClick: onCaptchaResume,
                  children: "Solved in Chrome — Resume Bot"
                }
              ) : /* @__PURE__ */ jsxRuntimeExports.jsx(
                "button",
                {
                  type: "button",
                  className: "btn btn-primary btn-sm",
                  id: "btn-notif-help-ack",
                  onClick: () => ackActiveHelpPopup("read"),
                  children: "Acknowledge"
                }
              )
            ] })
          ] })
        ] })
      }
    );
  };
  function formatNotifAge(tsVal) {
    const ts = safeFiniteNumber(tsVal, 0) ?? 0;
    if (ts <= 0) return "just now";
    const sec = Math.max(0, Math.round(Date.now() / 1e3 - ts));
    if (sec < 60) return `${sec}s ago`;
    if (sec < 3600) return `${Math.floor(sec / 60)}m ago`;
    if (sec < 86400) return `${Math.floor(sec / 3600)}h ago`;
    return `${Math.floor(sec / 86400)}d ago`;
  }
  const NotificationCenterModal = () => {
    const {
      notifications,
      notifCenterOpen,
      closeNotificationCenter,
      refreshNotificationCenter,
      uiPrefs,
      setUiPref,
      apiCommand: apiCommand2,
      showFeedback
    } = useConsole();
    const items = Array.isArray(notifications.items) ? notifications.items : [];
    const curFilter = uiPrefs.notifFilter || "active";
    const onFilterClick = (f) => {
      setUiPref("notifFilter", f);
      refreshNotificationCenter(f);
    };
    const onMarkAllRead = async () => {
      try {
        await apiCommand2({ command: "notifications_ack_all", status: "read" });
        await refreshNotificationCenter();
      } catch (err) {
        showFeedback(`Failed marking notifications read: ${err.message}`, feedbackKindForError(err));
      }
    };
    const onAckOne = async (nid, status) => {
      try {
        await apiCommand2({ command: "notification_ack", notification_id: nid, status });
        await refreshNotificationCenter();
      } catch (err) {
        showFeedback(`Failed updating notification: ${err.message}`, feedbackKindForError(err));
      }
    };
    return /* @__PURE__ */ jsxRuntimeExports.jsx(
      "div",
      {
        className: "notif-modal-backdrop",
        id: "notif-center-modal",
        role: "dialog",
        "aria-modal": "true",
        "aria-labelledby": "notif-center-title",
        hidden: !notifCenterOpen,
        children: /* @__PURE__ */ jsxRuntimeExports.jsxs("div", { className: "notif-modal-card notif-center-card", children: [
          /* @__PURE__ */ jsxRuntimeExports.jsxs("div", { className: "window-bar window-bar-wrap", children: [
            /* @__PURE__ */ jsxRuntimeExports.jsxs("div", { className: "window-title-group", children: [
              /* @__PURE__ */ jsxRuntimeExports.jsx("span", { className: "module-code tabular", children: "NTF" }),
              /* @__PURE__ */ jsxRuntimeExports.jsx("h2", { id: "notif-center-title", children: "Persistent System Notifications" })
            ] }),
            /* @__PURE__ */ jsxRuntimeExports.jsxs("div", { className: "inline-actions", children: [
              /* @__PURE__ */ jsxRuntimeExports.jsx("div", { className: "tab-group", children: [
                ["active", "Active"],
                ["unread", "Unread"],
                ["all", "All History"]
              ].map(([key, label]) => /* @__PURE__ */ jsxRuntimeExports.jsx(
                "button",
                {
                  type: "button",
                  className: `tab-btn${curFilter === key ? " active" : ""}`,
                  "data-notif-filter": key,
                  onClick: () => onFilterClick(key),
                  children: label
                },
                key
              )) }),
              /* @__PURE__ */ jsxRuntimeExports.jsx(
                "button",
                {
                  type: "button",
                  className: "btn btn-secondary btn-sm",
                  id: "btn-notif-mark-all",
                  onClick: onMarkAllRead,
                  children: "Mark All Read"
                }
              ),
              /* @__PURE__ */ jsxRuntimeExports.jsx(
                "button",
                {
                  type: "button",
                  className: "btn btn-secondary btn-sm",
                  id: "btn-notif-center-close",
                  onClick: closeNotificationCenter,
                  children: "Close"
                }
              )
            ] })
          ] }),
          /* @__PURE__ */ jsxRuntimeExports.jsx("div", { className: "notif-center-body", id: "notif-center-list", children: items.length === 0 ? /* @__PURE__ */ jsxRuntimeExports.jsx("p", { className: "muted", children: curFilter === "unread" ? "No unread notifications." : "No notifications recorded in SQLite yet." }) : items.map((n) => {
            const notifCode = formatNotificationCode(n);
            const sev = String(n.severity || "warning").toLowerCase();
            const cnt = Math.max(1, Math.round(safeFiniteNumber(n.occurrence_count, 1) ?? 1));
            const mParts = [];
            if (n.planet_coords) mParts.push(`[${n.planet_coords}]`);
            if (cnt > 1) mParts.push(`x${cnt}`);
            mParts.push(String(n.status || "unread").toUpperCase());
            mParts.push(formatNotifAge(n.updated_ts));
            return /* @__PURE__ */ jsxRuntimeExports.jsxs(
              "div",
              {
                className: `notif-item-row${n.status === "unread" ? " is-unread" : ""}`,
                "data-notification-id": n.id,
                children: [
                  /* @__PURE__ */ jsxRuntimeExports.jsxs("div", { className: "notif-item-head", children: [
                    /* @__PURE__ */ jsxRuntimeExports.jsxs("div", { className: "notif-item-title-wrap", children: [
                      /* @__PURE__ */ jsxRuntimeExports.jsx(
                        "span",
                        {
                          className: "notif-id-pill tabular",
                          "data-notif-code": notifCode,
                          title: "Notification ID — copy and share to troubleshoot",
                          children: notifCode
                        }
                      ),
                      /* @__PURE__ */ jsxRuntimeExports.jsx(
                        "span",
                        {
                          className: `notif-sev-pill tabular ${sev === "error" ? "sev-error" : "sev-warning"}`,
                          children: sev.toUpperCase()
                        }
                      ),
                      /* @__PURE__ */ jsxRuntimeExports.jsx("span", { className: "notif-item-title", children: String(n.title || "Notification") })
                    ] }),
                    /* @__PURE__ */ jsxRuntimeExports.jsx("span", { className: "notif-item-meta tabular", children: mParts.join(" · ") })
                  ] }),
                  n.message ? /* @__PURE__ */ jsxRuntimeExports.jsx("p", { className: "notif-item-msg tabular", children: String(n.message) }) : null,
                  n.help_prompt ? /* @__PURE__ */ jsxRuntimeExports.jsx("p", { className: "notif-item-help", children: String(n.help_prompt) }) : null,
                  /* @__PURE__ */ jsxRuntimeExports.jsxs("div", { className: "notif-item-actions", children: [
                    /* @__PURE__ */ jsxRuntimeExports.jsxs(
                      "button",
                      {
                        type: "button",
                        className: "btn btn-secondary btn-sm",
                        "data-copy-notif-id": notifCode,
                        onClick: () => {
                          void copyNotificationCode(notifCode, showFeedback);
                        },
                        children: [
                          "Copy ID (",
                          notifCode,
                          ")"
                        ]
                      }
                    ),
                    n.status === "unread" ? /* @__PURE__ */ jsxRuntimeExports.jsx(
                      "button",
                      {
                        type: "button",
                        className: "btn btn-secondary btn-sm",
                        onClick: () => onAckOne(n.id, "read"),
                        children: "Mark Read"
                      }
                    ) : null,
                    n.status !== "dismissed" ? /* @__PURE__ */ jsxRuntimeExports.jsx(
                      "button",
                      {
                        type: "button",
                        className: "btn btn-secondary btn-sm",
                        onClick: () => onAckOne(n.id, "dismissed"),
                        children: "Dismiss"
                      }
                    ) : null
                  ] })
                ]
              },
              n.id
            );
          }) })
        ] })
      }
    );
  };
  function clampPct(val) {
    const n = safeFiniteNumber(val, 0) ?? 0;
    return Math.max(0, Math.min(100, n));
  }
  function fmtIntLocal(n) {
    if (n === null || n === void 0 || n === "") return "0";
    const v = safeFiniteNumber(n, null);
    if (v === null) return "—";
    const clean = Math.abs(v) < 1e-3 && v < 0 && v > -0.05 ? 0 : v === 0 ? 0 : v;
    const formatted = clean.toLocaleString("en-US");
    return formatted === "-0" ? "0" : formatted;
  }
  function fmtCompactLocal(val) {
    const n = safeFiniteNumber(val, null);
    if (n === null) return "—";
    const absN = Math.abs(n);
    if (absN >= 1e6) return `${(n / 1e6).toFixed(2)}M`;
    if (absN >= 1e4) return `${(n / 1e3).toFixed(1)}k`;
    if (Number.isInteger(n)) return fmtIntLocal(n);
    return absN < 0.05 ? "0.0" : n.toFixed(1);
  }
  function fmtImprovementPct(rawPct, baseFit, bestFit) {
    let pct = safeFiniteNumber(rawPct, null);
    const base = safeFiniteNumber(baseFit, null);
    const best = safeFiniteNumber(bestFit, null);
    if (pct === null && base !== null && best !== null) {
      if (Math.abs(base) > 1e-6) {
        pct = (best - base) / Math.abs(base) * 100;
      } else if (Math.abs(best) <= 1e-12) {
        pct = 0;
      } else {
        pct = best > 0 ? 999.9 : -999.9;
      }
    }
    if (pct === null) return "—";
    if (pct > 999.9) return ">+999.9%";
    if (pct < -999.9) return "<-999.9%";
    if (Math.abs(pct) < 0.05) pct = 0;
    return `${pct >= 0 ? "+" : ""}${pct.toFixed(1)}%`;
  }
  function geneShortTag(kind) {
    const k = String(kind || "").trim().toUpperCase();
    if (!k) return "";
    if (k === "BUILD") return "BLD";
    if (k === "DEMOLISH_BUILDING") return "DEM";
    if (k === "WAIT_RESOURCES") return "WAIT";
    if (k === "RESEARCH") return "RES";
    if (k === "BUILD_SHIP") return "SHP";
    if (k === "ATTACK") return "ATK";
    if (k === "SPY") return "SPY";
    if (k === "RECYCLE") return "REC";
    if (k === "COLONIZE") return "COL";
    if (k === "SCAN_GALAXY") return "SCN";
    if (k === "THROTTLE_BUILDING") return "B2%";
    if (k === "CLAIM_TUTORIAL") return "TUT";
    return k.slice(0, 4);
  }
  const GaNnScannerHud = () => {
    var _a, _b, _c, _d, _e;
    const {
      dashboard,
      setDashboard,
      gaCalculating,
      beginGaCalc,
      endGaCalc,
      bumpDashboardSeq,
      lastOptimizerSummary,
      setLastOptimizerSummary,
      apiCommand: apiCommand2,
      loadDashboard,
      showFeedback
    } = useConsole();
    const cfgMode = String(
      ((_a = dashboard == null ? void 0 : dashboard.config) == null ? void 0 : _a.planner_mode) || (dashboard == null ? void 0 : dashboard.planner_mode) || "priority"
    ).toLowerCase();
    const isGaNn = cfgMode === "ga_nn";
    const incomingSum = (dashboard == null ? void 0 : dashboard.optimizer_summary) || (dashboard == null ? void 0 : dashboard.ga_nn_telemetry) || (dashboard == null ? void 0 : dashboard.optimizer_metrics) || null;
    const hasIncoming = Boolean(
      incomingSum && typeof incomingSum === "object" && Object.keys(incomingSum).length > 0
    );
    const rawSum = isGaNn ? (hasIncoming ? incomingSum : lastOptimizerSummary) || {} : {};
    const hasSummary = Boolean(rawSum && Object.keys(rawSum).length > 0);
    const cfgPop = Math.max(
      4,
      Math.round(safeFiniteNumber((_b = dashboard == null ? void 0 : dashboard.config) == null ? void 0 : _b.ga_population_size, 24) ?? 24)
    );
    const cfgGens = Math.max(
      1,
      Math.round(safeFiniteNumber((_c = dashboard == null ? void 0 : dashboard.config) == null ? void 0 : _c.ga_generations, 12) ?? 12)
    );
    const horizonSteps = Math.max(
      1,
      Math.round(safeFiniteNumber((_d = dashboard == null ? void 0 : dashboard.config) == null ? void 0 : _d.ga_horizon_steps, 6) ?? 6)
    );
    const statusPillText = !isGaNn ? "STANDBY" : gaCalculating ? "CALCULATING" : hasSummary ? "OPTIMIZED" : "ACTIVE";
    const popSize = Math.max(1, Math.round(safeFiniteNumber(rawSum.population_size, cfgPop) ?? cfgPop));
    const nnBackend = String(rawSum.nn_backend || "mlp").toUpperCase();
    const elapsedMs = safeFiniteNumber(rawSum.elapsed_ms, null);
    const safeElapsed = elapsedMs !== null ? Math.max(0, Math.round(elapsedMs)) : null;
    const metaLineText = !isGaNn ? "POP -- · NN --" : safeElapsed !== null ? `POP ${popSize} · ${nnBackend} · ${safeElapsed}ms` : `POP ${popSize} · ${nnBackend}`;
    const histLen = Array.isArray(rawSum.generation_history) ? rawSum.generation_history.length : null;
    const genDone = Math.max(
      0,
      Math.round(safeFiniteNumber(rawSum.generation ?? rawSum.generations ?? histLen, 0) ?? 0)
    );
    const genTarget = Math.max(genDone, cfgGens);
    const genPct = isGaNn ? clampPct(genTarget > 0 ? genDone / genTarget * 100 : 0) : 0;
    const seedVal = safeFiniteNumber(rawSum.seed ?? ((_e = dashboard == null ? void 0 : dashboard.config) == null ? void 0 : _e.ga_seed), null);
    const seedTxt = seedVal !== null ? `Seed ${Math.round(seedVal)}` : "Auto seed";
    const genSubText = !isGaNn ? "Waiting for optimizer pass" : gaCalculating ? "Evolving action sequences..." : `Horizon ${horizonSteps} steps · ${seedTxt}`;
    const nnEvals = Math.max(
      0,
      Math.round(safeFiniteNumber(rawSum.nn_evaluations ?? rawSum.neural_evals, 0) ?? 0)
    );
    const lossRaw = safeFiniteNumber(rawSum.nn_final_loss, null);
    const loss = lossRaw !== null ? Math.max(0, lossRaw) : null;
    const nnPct = !isGaNn ? 0 : gaCalculating ? 72 : nnEvals > 0 ? clampPct(Math.max(14, Math.log10(nnEvals + 1) * 38)) : 0;
    const nnSubText = !isGaNn ? "MSE Loss --" : loss !== null ? `MSE Loss ${loss.toFixed(4)} · ${nnBackend}` : `Backend ${nnBackend} · Horizon ${horizonSteps}`;
    const baseFit = safeFiniteNumber(rawSum.baseline_fitness ?? rawSum.initial_best_fitness, null);
    const bestFit = safeFiniteNumber(
      rawSum.best_fitness ?? rawSum.fitness_score ?? rawSum.final_best_fitness,
      null
    );
    const impText = isGaNn ? fmtImprovementPct(rawSum.improvement_pct, baseFit, bestFit) : "--%";
    const rawImp = safeFiniteNumber(rawSum.improvement_pct, null);
    const fitPct = !isGaNn ? 0 : impText.startsWith(">") ? 100 : clampPct(
      rawImp !== null ? Math.max(0, Math.min(100, rawImp + 15)) : bestFit !== null && bestFit > 0 ? 35 : 0
    );
    const fitSubText = isGaNn ? `Base ${fmtCompactLocal(baseFit)} · Best ${fmtCompactLocal(bestFit)}` : "Base -- · Best --";
    const chrom = Array.isArray(rawSum.best_chromosome) ? rawSum.best_chromosome : [];
    const planLen = Array.isArray(dashboard == null ? void 0 : dashboard.plan) ? dashboard.plan.length : 0;
    const gLen = Math.max(
      0,
      Math.round(
        safeFiniteNumber(rawSum.genome_length ?? rawSum.actions_count, chrom.length || planLen) ?? 0
      )
    );
    const genomePct = !isGaNn ? 0 : clampPct(horizonSteps > 0 ? Math.min(gLen, horizonSteps) / horizonSteps * 100 : 0);
    const seqPreview = (chrom.length > 0 ? chrom : (dashboard == null ? void 0 : dashboard.plan) || []).slice(0, 4).map((g) => geneShortTag(typeof g === "string" ? g : g == null ? void 0 : g.kind)).filter(Boolean).join(" → ");
    const genomeSubText = !isGaNn ? "Sequence standby" : seqPreview || (gLen > 0 ? `Top ${gLen} sequence steps` : "Sequence standby");
    const onRecalc = async () => {
      var _a2;
      beginGaCalc();
      try {
        showFeedback("Running GA + Neural Network sequence optimizer...", "ok");
        const res = await apiCommand2({ command: "plan", planner_mode: "ga_nn", refresh: false });
        bumpDashboardSeq();
        if (res.optimizer_summary) {
          setLastOptimizerSummary(res.optimizer_summary);
        }
        const curDash = ((_a2 = globalThis.state) == null ? void 0 : _a2.dashboard) || dashboard;
        if (curDash) {
          const updated = {
            ...curDash,
            plan: Array.isArray(res.actions) ? res.actions : curDash.plan,
            optimizer_summary: res.optimizer_summary ?? curDash.optimizer_summary
          };
          if (globalThis.state) {
            globalThis.state.dashboard = updated;
          }
          setDashboard(updated);
        } else {
          await loadDashboard({ refresh: false, preserveConfig: true, force: true });
        }
        showFeedback(
          `GA+NN evaluation complete (${(res.actions || []).length} planned actions).`,
          "ok"
        );
      } catch (err) {
        showFeedback(`GA+NN calculation failed: ${err.message}`, feedbackKindForError(err));
      } finally {
        endGaCalc();
      }
    };
    return /* @__PURE__ */ jsxRuntimeExports.jsxs(
      "article",
      {
        className: `hud-window gann-hud${isGaNn ? " is-active" : ""}${isGaNn && gaCalculating ? " is-calculating" : ""}`,
        id: "gann-calc-hud",
        "aria-label": "Genetic algorithm and neural network search telemetry",
        hidden: !isGaNn,
        children: [
          /* @__PURE__ */ jsxRuntimeExports.jsxs("div", { className: "window-bar window-bar-wrap", children: [
            /* @__PURE__ */ jsxRuntimeExports.jsxs("div", { className: "window-title-group", children: [
              /* @__PURE__ */ jsxRuntimeExports.jsx("span", { className: "module-code tabular", children: "GNN" }),
              /* @__PURE__ */ jsxRuntimeExports.jsx("h2", { children: "GA + Neural Search Scanner" }),
              /* @__PURE__ */ jsxRuntimeExports.jsx("span", { className: "gann-state-pill tabular", id: "gann-status-pill", role: "status", children: statusPillText })
            ] }),
            /* @__PURE__ */ jsxRuntimeExports.jsxs("div", { className: "inline-actions", children: [
              /* @__PURE__ */ jsxRuntimeExports.jsx("span", { className: "module-meta tabular", id: "gann-meta-line", children: metaLineText }),
              /* @__PURE__ */ jsxRuntimeExports.jsx(
                "button",
                {
                  type: "button",
                  className: "btn btn-secondary btn-sm",
                  id: "btn-gann-recalc",
                  onClick: onRecalc,
                  children: "Recalculate"
                }
              )
            ] })
          ] }),
          /* @__PURE__ */ jsxRuntimeExports.jsxs("div", { className: "gann-hud-body", id: "gann-hud-body", children: [
            /* @__PURE__ */ jsxRuntimeExports.jsx("div", { className: "gann-scanner-stage", "aria-hidden": "true", children: /* @__PURE__ */ jsxRuntimeExports.jsxs(
              "svg",
              {
                id: "gann-radial-svg",
                className: "gann-radial-svg",
                viewBox: "0 0 220 160",
                width: "180",
                height: "130",
                focusable: "false",
                children: [
                  /* @__PURE__ */ jsxRuntimeExports.jsx(
                    "path",
                    {
                      className: "gann-axis-rail",
                      d: "M 110 14 L 110 146",
                      fill: "none",
                      stroke: "#244249",
                      strokeWidth: "1",
                      strokeDasharray: "3 3"
                    }
                  ),
                  /* @__PURE__ */ jsxRuntimeExports.jsxs("g", { transform: "translate(110, 120) scale(1, 0.52)", children: [
                    /* @__PURE__ */ jsxRuntimeExports.jsx("circle", { cx: "0", cy: "0", r: "80", fill: "none", stroke: "#18343b", strokeWidth: "1" }),
                    /* @__PURE__ */ jsxRuntimeExports.jsxs("g", { className: "gann-ring-mid", children: [
                      /* @__PURE__ */ jsxRuntimeExports.jsx(
                        "circle",
                        {
                          cx: "0",
                          cy: "0",
                          r: "66",
                          fill: "none",
                          stroke: "#16ec98",
                          strokeOpacity: "0.48",
                          strokeWidth: "2",
                          strokeDasharray: "42 16 18 14"
                        }
                      ),
                      /* @__PURE__ */ jsxRuntimeExports.jsx(
                        "circle",
                        {
                          cx: "0",
                          cy: "0",
                          r: "46",
                          fill: "none",
                          stroke: "#32e8ff",
                          strokeOpacity: "0.38",
                          strokeWidth: "1.5",
                          strokeDasharray: "20 12"
                        }
                      )
                    ] })
                  ] }),
                  /* @__PURE__ */ jsxRuntimeExports.jsxs("g", { transform: "translate(110, 80) scale(1, 0.52)", children: [
                    /* @__PURE__ */ jsxRuntimeExports.jsx("circle", { cx: "0", cy: "0", r: "84", fill: "none", stroke: "#243135", strokeWidth: "1" }),
                    /* @__PURE__ */ jsxRuntimeExports.jsxs("g", { className: "gann-ring-gold", children: [
                      /* @__PURE__ */ jsxRuntimeExports.jsx(
                        "circle",
                        {
                          cx: "0",
                          cy: "0",
                          r: "52",
                          fill: "none",
                          stroke: "#efb03b",
                          strokeWidth: "2.4",
                          strokeDasharray: "22 8 10 8 34 10"
                        }
                      ),
                      /* @__PURE__ */ jsxRuntimeExports.jsx("circle", { cx: "52", cy: "0", r: "3", fill: "#efb03b" }),
                      /* @__PURE__ */ jsxRuntimeExports.jsx("circle", { cx: "-52", cy: "0", r: "3", fill: "#efb03b" }),
                      /* @__PURE__ */ jsxRuntimeExports.jsx("circle", { cx: "0", cy: "52", r: "2.5", fill: "#ffcf5c" }),
                      /* @__PURE__ */ jsxRuntimeExports.jsx("circle", { cx: "0", cy: "-52", r: "2.5", fill: "#ffcf5c" })
                    ] }),
                    /* @__PURE__ */ jsxRuntimeExports.jsxs("g", { className: "gann-core-pulse", children: [
                      /* @__PURE__ */ jsxRuntimeExports.jsx("circle", { cx: "0", cy: "0", r: "26", fill: "#091518", stroke: "#16ec98", strokeWidth: "1.8" }),
                      /* @__PURE__ */ jsxRuntimeExports.jsx(
                        "circle",
                        {
                          cx: "0",
                          cy: "0",
                          r: "14",
                          fill: "none",
                          stroke: "#ffcf5c",
                          strokeOpacity: "0.7",
                          strokeWidth: "1.2"
                        }
                      ),
                      /* @__PURE__ */ jsxRuntimeExports.jsx("circle", { cx: "0", cy: "0", r: "7", fill: "#efb03b" })
                    ] })
                  ] }),
                  /* @__PURE__ */ jsxRuntimeExports.jsxs("g", { transform: "translate(110, 42) scale(1, 0.52)", children: [
                    /* @__PURE__ */ jsxRuntimeExports.jsx(
                      "circle",
                      {
                        cx: "0",
                        cy: "0",
                        r: "84",
                        fill: "none",
                        stroke: "#2f6670",
                        strokeWidth: "4",
                        strokeDasharray: "1.5 6.0"
                      }
                    ),
                    /* @__PURE__ */ jsxRuntimeExports.jsxs("g", { className: "gann-ring-outer", children: [
                      /* @__PURE__ */ jsxRuntimeExports.jsx(
                        "circle",
                        {
                          cx: "0",
                          cy: "0",
                          r: "72",
                          fill: "none",
                          stroke: "#32e8ff",
                          strokeWidth: "2",
                          strokeDasharray: "58 14 26 12 44 18"
                        }
                      ),
                      /* @__PURE__ */ jsxRuntimeExports.jsx("circle", { cx: "0", cy: "-72", r: "3", fill: "#32e8ff" }),
                      /* @__PURE__ */ jsxRuntimeExports.jsx("circle", { cx: "0", cy: "72", r: "3", fill: "#16ec98" })
                    ] }),
                    /* @__PURE__ */ jsxRuntimeExports.jsxs("g", { className: "gann-scan-sweep", children: [
                      /* @__PURE__ */ jsxRuntimeExports.jsx(
                        "path",
                        {
                          d: "M 0 -60 A 60 60 0 0 1 60 0",
                          fill: "none",
                          stroke: "#16ec98",
                          strokeWidth: "3",
                          strokeLinecap: "round"
                        }
                      ),
                      /* @__PURE__ */ jsxRuntimeExports.jsx(
                        "path",
                        {
                          d: "M 0 -46 L 0 -22 M 0 22 L 0 46 M -46 0 L -22 0 M 22 0 L 46 0",
                          fill: "none",
                          stroke: "#32e8ff",
                          strokeOpacity: "0.72",
                          strokeWidth: "1.2"
                        }
                      )
                    ] })
                  ] })
                ]
              }
            ) }),
            /* @__PURE__ */ jsxRuntimeExports.jsxs("div", { className: "gann-metrics-grid", children: [
              /* @__PURE__ */ jsxRuntimeExports.jsxs("div", { className: "gann-metric-cell", children: [
                /* @__PURE__ */ jsxRuntimeExports.jsxs("div", { className: "gann-metric-head", children: [
                  /* @__PURE__ */ jsxRuntimeExports.jsx(
                    "div",
                    {
                      className: "gann-mini-arc is-cyan",
                      id: "gann-arc-gen",
                      "aria-hidden": "true",
                      style: { ["--arc-pct"]: String(genPct) }
                    }
                  ),
                  /* @__PURE__ */ jsxRuntimeExports.jsxs("div", { className: "gann-metric-titles", children: [
                    /* @__PURE__ */ jsxRuntimeExports.jsx("span", { className: "gann-metric-label", children: "Generation Progress" }),
                    /* @__PURE__ */ jsxRuntimeExports.jsx("strong", { className: "gann-metric-val tabular", id: "gann-gen-val", children: isGaNn ? `${genDone} / ${genTarget}` : "0 / --" })
                  ] })
                ] }),
                /* @__PURE__ */ jsxRuntimeExports.jsx("div", { className: "meter-track", children: /* @__PURE__ */ jsxRuntimeExports.jsx(
                  "div",
                  {
                    className: "meter-fill gann-bar-cyan",
                    id: "gann-gen-bar",
                    style: { transform: `scaleX(${genPct / 100})` }
                  }
                ) }),
                /* @__PURE__ */ jsxRuntimeExports.jsx("span", { className: "gann-metric-sub tabular", id: "gann-gen-sub", children: genSubText })
              ] }),
              /* @__PURE__ */ jsxRuntimeExports.jsxs("div", { className: "gann-metric-cell", children: [
                /* @__PURE__ */ jsxRuntimeExports.jsxs("div", { className: "gann-metric-head", children: [
                  /* @__PURE__ */ jsxRuntimeExports.jsx(
                    "div",
                    {
                      className: "gann-mini-arc is-mint",
                      id: "gann-arc-nn",
                      "aria-hidden": "true",
                      style: { ["--arc-pct"]: String(nnPct) }
                    }
                  ),
                  /* @__PURE__ */ jsxRuntimeExports.jsxs("div", { className: "gann-metric-titles", children: [
                    /* @__PURE__ */ jsxRuntimeExports.jsx("span", { className: "gann-metric-label", children: "Neural Eval Pass" }),
                    /* @__PURE__ */ jsxRuntimeExports.jsx("strong", { className: "gann-metric-val tabular", id: "gann-nn-val", children: isGaNn ? `${fmtIntLocal(nnEvals)} evals` : "0 evals" })
                  ] })
                ] }),
                /* @__PURE__ */ jsxRuntimeExports.jsx("div", { className: "meter-track", children: /* @__PURE__ */ jsxRuntimeExports.jsx(
                  "div",
                  {
                    className: "meter-fill gann-bar-mint",
                    id: "gann-nn-bar",
                    style: { transform: `scaleX(${nnPct / 100})` }
                  }
                ) }),
                /* @__PURE__ */ jsxRuntimeExports.jsx("span", { className: "gann-metric-sub tabular", id: "gann-nn-sub", children: nnSubText })
              ] }),
              /* @__PURE__ */ jsxRuntimeExports.jsxs("div", { className: "gann-metric-cell", children: [
                /* @__PURE__ */ jsxRuntimeExports.jsxs("div", { className: "gann-metric-head", children: [
                  /* @__PURE__ */ jsxRuntimeExports.jsx(
                    "div",
                    {
                      className: "gann-mini-arc is-gold",
                      id: "gann-arc-fit",
                      "aria-hidden": "true",
                      style: { ["--arc-pct"]: String(fitPct) }
                    }
                  ),
                  /* @__PURE__ */ jsxRuntimeExports.jsxs("div", { className: "gann-metric-titles", children: [
                    /* @__PURE__ */ jsxRuntimeExports.jsx("span", { className: "gann-metric-label", children: "Fitness Improvement" }),
                    /* @__PURE__ */ jsxRuntimeExports.jsx("strong", { className: "gann-metric-val tabular", id: "gann-fit-val", children: impText })
                  ] })
                ] }),
                /* @__PURE__ */ jsxRuntimeExports.jsx("div", { className: "meter-track", children: /* @__PURE__ */ jsxRuntimeExports.jsx(
                  "div",
                  {
                    className: "meter-fill gann-bar-gold",
                    id: "gann-fit-bar",
                    style: { transform: `scaleX(${fitPct / 100})` }
                  }
                ) }),
                /* @__PURE__ */ jsxRuntimeExports.jsx("span", { className: "gann-metric-sub tabular", id: "gann-fit-sub", children: fitSubText })
              ] }),
              /* @__PURE__ */ jsxRuntimeExports.jsxs("div", { className: "gann-metric-cell", children: [
                /* @__PURE__ */ jsxRuntimeExports.jsxs("div", { className: "gann-metric-head", children: [
                  /* @__PURE__ */ jsxRuntimeExports.jsx(
                    "div",
                    {
                      className: "gann-mini-arc is-cyan",
                      id: "gann-arc-genome",
                      "aria-hidden": "true",
                      style: { ["--arc-pct"]: String(genomePct) }
                    }
                  ),
                  /* @__PURE__ */ jsxRuntimeExports.jsxs("div", { className: "gann-metric-titles", children: [
                    /* @__PURE__ */ jsxRuntimeExports.jsx("span", { className: "gann-metric-label", children: "Genome Search" }),
                    /* @__PURE__ */ jsxRuntimeExports.jsx("strong", { className: "gann-metric-val tabular", id: "gann-genome-val", children: isGaNn ? `${gLen} genes` : "0 genes" })
                  ] })
                ] }),
                /* @__PURE__ */ jsxRuntimeExports.jsx("div", { className: "meter-track", children: /* @__PURE__ */ jsxRuntimeExports.jsx(
                  "div",
                  {
                    className: "meter-fill gann-bar-cyan",
                    id: "gann-genome-bar",
                    style: { transform: `scaleX(${genomePct / 100})` }
                  }
                ) }),
                /* @__PURE__ */ jsxRuntimeExports.jsx("span", { className: "gann-metric-sub tabular", id: "gann-genome-sub", children: genomeSubText })
              ] })
            ] })
          ] })
        ]
      }
    );
  };
  const MQ_BUILDINGS_CATALOG = [
    { id: 1, name: "Pig iron mine" },
    { id: 2, name: "Blast furnace" },
    { id: 3, name: "Kryptonite mine" },
    { id: 4, name: "Spice mine" },
    { id: 5, name: "Fusion plant" },
    { id: 6, name: "Thermosolar plant" },
    { id: 7, name: "Development center" },
    { id: 8, name: "Research lab" },
    { id: 9, name: "Weapons factory" },
    { id: 10, name: "Fleet base" },
    { id: 11, name: "Pig iron warehouse" },
    { id: 12, name: "Metal warehouse" },
    { id: 13, name: "Kryptonite warehouse" },
    { id: 14, name: "Spice warehouse" },
    { id: 15, name: "Particle cannon" },
    { id: 16, name: "Particle shield" },
    { id: 17, name: "Microsystem accelerator" },
    { id: 18, name: "Teleporter" },
    { id: 19, name: "Galaxy scanner" },
    { id: 20, name: "Tunnel system" },
    { id: 21, name: "Quantum Array" }
  ];
  const MQ_RESEARCH_CATALOG = [
    { id: 1, name: "Mining technology" },
    { id: 2, name: "Energy technology" },
    { id: 3, name: "Engine research" },
    { id: 4, name: "Rocket engine" },
    { id: 5, name: "Diffusion engine" },
    { id: 6, name: "Warp engine" },
    { id: 7, name: "Spy technology" },
    { id: 8, name: "Weapons technology" },
    { id: 9, name: "Shield technology" },
    { id: 10, name: "Defense systems" },
    { id: 11, name: "Targeting systems" },
    { id: 12, name: "Particle analyzer" },
    { id: 13, name: "Graviton technology" },
    { id: 14, name: "Teleportation" },
    { id: 15, name: "Black hole research" }
  ];
  const MQ_SHIPS_CATALOG = [
    { id: 1, name: "Small transport", key: "s1" },
    { id: 2, name: "Large transport", key: "s2" },
    { id: 3, name: "Transmitter", key: "s3" },
    { id: 4, name: "Small fighter", key: "s4" },
    { id: 5, name: "Warship", key: "s5" },
    { id: 6, name: "Starcruiser", key: "s6" },
    { id: 7, name: "Phoenix", key: "s7" },
    { id: 8, name: "Colony ship", key: "s8" },
    { id: 9, name: "Solar cells", key: "s9" },
    { id: 10, name: "Spy probe", key: "s10" },
    { id: 11, name: "Recycler", key: "s11" },
    { id: 13, name: "Battleship", key: "s13" },
    { id: 14, name: "Stealth bomber", key: "s14" },
    { id: 15, name: "Destroyer", key: "s15" },
    { id: 16, name: "Imperial starbase", key: "s16" }
  ];
  const DASH_FLEET_KINDS = /* @__PURE__ */ new Set([
    "BUILD_SHIP",
    "SHIP",
    "BUILD_DEFENSE",
    "DEFENSE",
    "SPY",
    "ATTACK",
    "RECYCLE",
    "COLONIZE",
    "SCAN_GALAXY",
    "ESCAPE_FLIGHT",
    "ALERT_HOSTILE"
  ]);
  function dashboardQueueDomainForAction(act) {
    const kind = String((act == null ? void 0 : act.kind) || "").toUpperCase();
    if (kind === "RESEARCH") return "research";
    if (DASH_FLEET_KINDS.has(kind)) return "fleet";
    return "building";
  }
  function dashboardQueueDomainForJob(job) {
    const cat = String((job == null ? void 0 : job.category) || "").toLowerCase();
    if (cat === "research") return "research";
    if (cat === "fleet" || cat === "shipyard" || cat === "defense") return "fleet";
    return "building";
  }
  function cleanQueueJobTitle(job) {
    const rawName = String((job == null ? void 0 : job.name) || "Active job").replace(/^(?:cancel|abort)\s+/i, "").replace(/\s*\(\s*Level\s+\d+\s*\)\s*$/i, "").trim();
    const lvl = finiteNum(job == null ? void 0 : job.target_level, null);
    if (lvl !== null && lvl > 0) {
      return `${rawName} → L${lvl}`;
    }
    return rawName || "Active job";
  }
  function actionPayloadFor(action, dryRun) {
    if (!action) return null;
    if (action.kind === "CLAIM_TUTORIAL") {
      return { command: "claim_tutorial", dry_run: dryRun };
    }
    if (action.kind === "BUILD" && action.target_id != null) {
      return {
        command: "build",
        building_id: action.target_id,
        planet_id: action.planet_id,
        dry_run: dryRun
      };
    }
    if (action.kind === "DEMOLISH_BUILDING" && action.target_id != null) {
      return {
        command: "demolish_building",
        building_id: action.target_id,
        planet_id: action.planet_id,
        dry_run: dryRun
      };
    }
    if (action.kind === "RESEARCH" && action.target_id != null) {
      return {
        command: "research",
        research_id: action.target_id,
        planet_id: action.planet_id,
        dry_run: dryRun
      };
    }
    if (action.kind === "BUILD_SHIP" && action.target_id != null) {
      return {
        command: "build_ship",
        ship_id: action.target_id,
        quantity: Math.max(1, Math.round(finiteNum(action.target_level, 1))),
        planet_id: action.planet_id,
        dry_run: dryRun
      };
    }
    if (action.kind === "THROTTLE_BUILDING" && action.target_id != null) {
      return {
        command: "throttle_building",
        building_id: action.target_id,
        percentage: action.target_level == null ? 0 : finiteNum(action.target_level, 0),
        planet_id: action.planet_id,
        dry_run: dryRun
      };
    }
    if (action.kind === "ESCAPE_FLIGHT") {
      return { command: "fleetsave_plan", planet_id: action.planet_id };
    }
    if (action.kind === "SCAN_GALAXY") {
      const m = /(\d{1,2}):(\d{1,3})/.exec(action.target_name || action.planet_coords || "");
      return {
        command: "scan_galaxy",
        galaxy: m ? Number(m[1]) : void 0,
        system: m ? Number(m[2]) : void 0,
        planet_id: action.planet_id,
        dry_run: dryRun
      };
    }
    if (action.kind === "ATTACK") {
      const m = /(\d{1,2}:\d{1,3}:\d{1,2})/.exec(action.target_name || "");
      return {
        command: "attack",
        target_coords: m ? m[1] : action.target_name,
        planet_id: action.planet_id,
        dry_run: dryRun
      };
    }
    if (action.kind === "SPY") {
      const m = /(\d{1,2}:\d{1,3}:\d{1,2})/.exec(action.target_name || "");
      return {
        command: "spy",
        target_coords: m ? m[1] : action.target_name,
        quantity: Math.max(1, Math.round(finiteNum(action.target_level, 1))),
        planet_id: action.planet_id,
        dry_run: dryRun
      };
    }
    if (action.kind === "RECYCLE") {
      const m = /(\d{1,2}:\d{1,3}:\d{1,2})/.exec(action.target_name || "");
      return {
        command: "recycle",
        target_coords: m ? m[1] : action.target_name,
        quantity: Math.max(1, Math.round(finiteNum(action.target_level, 1))),
        planet_id: action.planet_id,
        dry_run: dryRun
      };
    }
    if (action.kind === "COLONIZE") {
      const m = /(\d{1,2}:\d{1,3}:\d{1,2})/.exec(action.target_name || "");
      return {
        command: "colonize",
        target_coords: m ? m[1] : action.target_name,
        planet_id: action.planet_id,
        dry_run: dryRun
      };
    }
    return null;
  }
  function buildPinPayloadFromAction(act, priority = "P1") {
    const normKind = act.kind === "WAIT_RESOURCES" ? "BUILD" : String(act.kind || "").toUpperCase();
    const mCoords = /(\d{1,2}:\d{1,3}(?::\d{1,2})?)/.exec(act.target_name || "");
    return {
      kind: normKind,
      priority,
      position: "top",
      planet_id: act.planet_id || 0,
      target_id: act.target_id ?? void 0,
      target_name: act.target_name || "",
      target_coords: mCoords ? mCoords[1] : void 0,
      target_level: act.target_level ?? void 0,
      deduplicate_pending: true
    };
  }
  const PlanActionRow = ({
    action: act,
    compact = false,
    nowEpochSec,
    onDispatch,
    onUpdateQueue,
    onCancelQueue,
    onPinQueue
  }) => {
    const isManual = act.manual_queue_id != null && Number(act.manual_queue_id) > 0;
    const qid = isManual ? Number(act.manual_queue_id) : 0;
    const prioVal = String(act.priority || "?");
    const prioSelectRef = reactExports.useRef(null);
    const handledEventsRef = reactExports.useRef(/* @__PURE__ */ new WeakSet());
    reactExports.useEffect(() => {
      const sel = prioSelectRef.current;
      if (!sel || !isManual || compact) return;
      const onNativeChange = (ev) => {
        if (handledEventsRef.current.has(ev)) return;
        handledEventsRef.current.add(ev);
        const nextVal = sel.value;
        if (nextVal && nextVal !== prioVal) {
          onUpdateQueue(qid, { priority: nextVal, move: "top" });
        }
      };
      sel.addEventListener("change", onNativeChange);
      return () => sel.removeEventListener("change", onNativeChange);
    }, [compact, isManual, onUpdateQueue, prioVal, qid]);
    let lvlStr = "";
    if (act.kind === "THROTTLE_BUILDING" && act.target_level != null) {
      lvlStr = ` (${act.target_level}%)`;
    } else if (act.kind === "BUILD_SHIP" && act.target_level) {
      lvlStr = ` x${act.target_level}`;
    } else if (act.target_level != null) {
      lvlStr = ` → L${act.target_level}`;
    }
    const costStr = formatResourcesInline(act.cost);
    const missStr = formatResourcesInline(act.missing);
    const hasRawWait = act.wait_eta !== void 0 && act.wait_eta !== null && String(act.wait_eta).trim() !== "";
    const isBlocked = hasRawWait && String(act.wait_eta) === "—" || act.wait_hours === null || !hasRawWait && !act.executable_now && (act.wait_hours == null || !Number.isFinite(Number(act.wait_hours)) || Number(act.wait_hours) <= 0);
    const waitHoursNum = act.wait_hours !== void 0 && act.wait_hours !== null && Number.isFinite(Number(act.wait_hours)) ? Number(act.wait_hours) : 0;
    const waitEta = hasRawWait ? String(act.wait_eta) : isBlocked ? "—" : act.executable_now ? "Ready" : "—";
    const bnTag = bottleneckTag(act.eta_bottleneck);
    const isWaiting = !isBlocked && (waitHoursNum > 0 || waitEta !== "Ready" && waitEta !== "Instant" && waitEta !== "—");
    const waitPillText = (isBlocked || isWaiting) && bnTag ? `${waitEta} (${bnTag})` : waitEta;
    const hasRawTotal = act.total_eta !== void 0 && act.total_eta !== null && String(act.total_eta).trim() !== "";
    const hasRawDur = act.duration_text !== void 0 && act.duration_text !== null && String(act.duration_text).trim() !== "";
    const durText = hasRawDur ? String(act.duration_text) : "";
    const totalBlocked = isBlocked || hasRawTotal && String(act.total_eta) === "—" || act.total_hours === null;
    const totalEta = hasRawTotal ? String(act.total_eta) : totalBlocked ? "—" : durText || (act.executable_now ? "Ready" : "—");
    const totSecNum = finiteNum(act.total_seconds, null);
    const effNowSec = nowEpochSec != null && nowEpochSec > 0 ? nowEpochSec : Math.floor(Date.now() / 1e3);
    const cmdTemplate = actionPayloadFor(act, false);
    const canPin = Boolean(
      actionPayloadFor(act.kind === "WAIT_RESOURCES" ? { ...act, kind: "BUILD" } : act, true)
    );
    const targetLabel = String(act.target_name || "command").replace(/>/g, "→");
    return /* @__PURE__ */ jsxRuntimeExports.jsxs("tr", { className: isManual ? "is-manual-queued" : void 0, children: [
      /* @__PURE__ */ jsxRuntimeExports.jsx("td", { children: /* @__PURE__ */ jsxRuntimeExports.jsxs("div", { className: "prio-cell-stack", children: [
        /* @__PURE__ */ jsxRuntimeExports.jsxs("span", { className: `prio-tag prio-${prioVal}`, children: [
          /* @__PURE__ */ jsxRuntimeExports.jsx("span", { className: "prio-dot" }),
          prioVal
        ] }),
        isManual ? /* @__PURE__ */ jsxRuntimeExports.jsxs("span", { className: "mq-pin-badge tabular", children: [
          "PIN #",
          act.manual_queue_id
        ] }) : null
      ] }) }),
      /* @__PURE__ */ jsxRuntimeExports.jsxs("td", { children: [
        /* @__PURE__ */ jsxRuntimeExports.jsx("div", { className: "action-title", children: `${act.target_name || "Action"}${lvlStr}` }),
        /* @__PURE__ */ jsxRuntimeExports.jsx("div", { className: "action-sub", children: `${act.kind || "ACTION"} · [${act.planet_coords || "—"}]` })
      ] }),
      /* @__PURE__ */ jsxRuntimeExports.jsxs("td", { className: "tabular", children: [
        /* @__PURE__ */ jsxRuntimeExports.jsx("div", { children: costStr }),
        missStr !== "0" && missStr !== "—" ? /* @__PURE__ */ jsxRuntimeExports.jsx("div", { className: "action-sub", children: `Missing: ${missStr}` }) : null
      ] }),
      /* @__PURE__ */ jsxRuntimeExports.jsx("td", { className: "col-right tabular", children: /* @__PURE__ */ jsxRuntimeExports.jsx(
        "span",
        {
          className: `eta-pill${isBlocked ? " is-blocked" : isWaiting ? " is-wait" : ""}`,
          title: bnTag ? `Bottleneck: ${bnTag}` : void 0,
          children: waitPillText
        }
      ) }),
      /* @__PURE__ */ jsxRuntimeExports.jsxs("td", { className: "col-right tabular", children: [
        /* @__PURE__ */ jsxRuntimeExports.jsx(
          "span",
          {
            className: `eta-pill${totalBlocked ? " is-blocked" : isWaiting ? " is-wait" : ""}`,
            children: totalEta
          }
        ),
        compact && !totalBlocked && totSecNum !== null && totSecNum > 0 ? /* @__PURE__ */ jsxRuntimeExports.jsx("div", { className: "finish-clock-sub tabular", children: `at ${fmtClockTime(effNowSec + totSecNum, effNowSec)}` }) : !compact && durText && durText !== totalEta ? /* @__PURE__ */ jsxRuntimeExports.jsx("div", { className: "action-sub", children: durText === "Instant" ? "Instant" : `Dur: ${durText}` }) : null
      ] }),
      !compact ? /* @__PURE__ */ jsxRuntimeExports.jsx("td", { children: act.reason || "" }) : null,
      /* @__PURE__ */ jsxRuntimeExports.jsx("td", { className: "col-right", children: /* @__PURE__ */ jsxRuntimeExports.jsxs("div", { className: "row-actions-wrap", children: [
        cmdTemplate && act.executable_now ? /* @__PURE__ */ jsxRuntimeExports.jsxs(jsxRuntimeExports.Fragment, { children: [
          /* @__PURE__ */ jsxRuntimeExports.jsx(
            "button",
            {
              type: "button",
              className: "btn btn-primary btn-sm",
              onClick: () => onDispatch(act, false),
              children: "Run"
            }
          ),
          /* @__PURE__ */ jsxRuntimeExports.jsx(
            "button",
            {
              type: "button",
              className: "btn btn-secondary btn-sm",
              onClick: () => onDispatch(act, true),
              children: "Sim"
            }
          )
        ] }) : !isManual ? /* @__PURE__ */ jsxRuntimeExports.jsx("span", { className: "action-sub", children: "Waiting" }) : null,
        isManual ? /* @__PURE__ */ jsxRuntimeExports.jsxs(jsxRuntimeExports.Fragment, { children: [
          !compact ? /* @__PURE__ */ jsxRuntimeExports.jsxs(jsxRuntimeExports.Fragment, { children: [
            /* @__PURE__ */ jsxRuntimeExports.jsx(
              "select",
              {
                ref: prioSelectRef,
                className: "mq-prio-select",
                value: prioVal,
                "aria-label": `Priority for ${targetLabel} (#${qid})`,
                onChange: (e) => {
                  handledEventsRef.current.add(e.nativeEvent);
                  const nextVal = e.target.value;
                  if (nextVal && nextVal !== prioVal) {
                    onUpdateQueue(qid, { priority: nextVal, move: "top" });
                  }
                },
                children: ["P0", "P1", "P2", "P3"].map((pOpt) => /* @__PURE__ */ jsxRuntimeExports.jsx("option", { value: pOpt, children: pOpt }, pOpt))
              }
            ),
            /* @__PURE__ */ jsxRuntimeExports.jsx(
              "button",
              {
                type: "button",
                className: "btn btn-secondary btn-sm",
                title: "Move to top of priority band",
                "aria-label": `Move ${targetLabel} (#${qid}) to top of ${prioVal}`,
                onClick: () => onUpdateQueue(qid, { move: "top" }),
                children: "Top"
              }
            )
          ] }) : null,
          /* @__PURE__ */ jsxRuntimeExports.jsx(
            "button",
            {
              type: "button",
              className: "btn btn-secondary btn-sm",
              title: "Move up in queue",
              "aria-label": `Move ${targetLabel} (#${qid}) up in queue`,
              onClick: () => onUpdateQueue(qid, { move: "up" }),
              children: "Up"
            }
          ),
          !compact ? /* @__PURE__ */ jsxRuntimeExports.jsx(
            "button",
            {
              type: "button",
              className: "btn btn-secondary btn-sm",
              title: "Move down in queue",
              "aria-label": `Move ${targetLabel} (#${qid}) down in queue`,
              onClick: () => onUpdateQueue(qid, { move: "down" }),
              children: "Dn"
            }
          ) : null,
          /* @__PURE__ */ jsxRuntimeExports.jsx(
            "button",
            {
              type: "button",
              className: "btn btn-secondary btn-sm btn-danger-ghost",
              title: "Remove from manual queue",
              "aria-label": `Remove ${targetLabel} (#${qid}) from manual queue`,
              onClick: () => onCancelQueue(qid),
              children: "Drop"
            }
          )
        ] }) : canPin ? /* @__PURE__ */ jsxRuntimeExports.jsx(
          "button",
          {
            type: "button",
            className: "btn btn-secondary btn-sm",
            title: "Pin and prioritize at top of P1 queue",
            "aria-label": `Pin ${targetLabel} to P1 queue`,
            onClick: () => onPinQueue(act, "P1"),
            children: "Pin P1"
          }
        ) : null
      ] }) })
    ] });
  };
  const ActiveExecJobRow = ({ job, nowEpochSec }) => {
    const slot = Math.max(1, Math.trunc(finiteNum(job == null ? void 0 : job.queue_slot, 1)));
    const detailText = String((job == null ? void 0 : job.detail) || "").trim() || (slot > 1 ? `In-game queue #${slot}` : "Executing now");
    const finTs = finiteNum(job == null ? void 0 : job.finish_ts, null);
    const effNowSec = nowEpochSec != null && nowEpochSec > 0 ? nowEpochSec : Math.floor(Date.now() / 1e3);
    const remSec = finTs !== null && finTs > 0 ? Math.max(0, Math.round(finTs - effNowSec)) : finiteNum(job == null ? void 0 : job.remaining_seconds, null);
    let remDisplay = String((job == null ? void 0 : job.remaining_text) || "").trim() || "Running";
    if (finTs !== null && finTs > 0) {
      remDisplay = remSec !== null && remSec > 0 ? fmtHms(remSec) : "Finishing...";
    }
    const finDisplay = finTs !== null && finTs > 0 ? fmtClockTime(finTs, effNowSec) : "In progress";
    const detLow = String((job == null ? void 0 : job.detail) || "").toLowerCase();
    let liveLabel = "Executing";
    if (slot > 1) {
      liveLabel = `Queued #${slot}`;
    } else if (detLow.includes("returning")) {
      liveLabel = "Returning";
    } else if (detLow.includes("outbound") || detLow.includes("flight")) {
      liveLabel = "In flight";
    }
    return /* @__PURE__ */ jsxRuntimeExports.jsxs("tr", { className: slot > 1 ? "dash-exec-row is-queued-slot" : "dash-exec-row", children: [
      /* @__PURE__ */ jsxRuntimeExports.jsx("td", { children: /* @__PURE__ */ jsxRuntimeExports.jsx("span", { className: slot > 1 ? "prio-badge prio-game-q tabular" : "prio-badge prio-exec tabular", children: slot > 1 ? `GAME #${slot}` : "EXEC" }) }),
      /* @__PURE__ */ jsxRuntimeExports.jsx("td", { children: /* @__PURE__ */ jsxRuntimeExports.jsx("div", { className: "action-title", children: cleanQueueJobTitle(job) }) }),
      /* @__PURE__ */ jsxRuntimeExports.jsx("td", { children: /* @__PURE__ */ jsxRuntimeExports.jsx("span", { className: "action-sub", children: detailText }) }),
      /* @__PURE__ */ jsxRuntimeExports.jsx("td", { className: "col-right tabular", children: /* @__PURE__ */ jsxRuntimeExports.jsx(
        "span",
        {
          className: "eta-pill is-exec-timer",
          "data-finish-ts": finTs !== null && finTs > 0 ? String(finTs) : void 0,
          children: remDisplay
        }
      ) }),
      /* @__PURE__ */ jsxRuntimeExports.jsx("td", { className: "col-right tabular", children: /* @__PURE__ */ jsxRuntimeExports.jsx("span", { className: "eta-pill is-finish-at", children: finDisplay }) }),
      /* @__PURE__ */ jsxRuntimeExports.jsx("td", { className: "col-right", children: /* @__PURE__ */ jsxRuntimeExports.jsx("span", { className: slot > 1 ? "exec-live-label is-slot" : "exec-live-label", children: liveLabel }) })
    ] });
  };
  const DashboardQueueCard = ({
    cardId,
    moduleCode,
    title,
    countId,
    addBtnId,
    addBtnAriaLabel,
    onAddQueueClick,
    gotoLabel,
    gotoBtnId,
    gotoPageAttr,
    onGotoClick,
    execTbodyId,
    planTbodyId,
    execJobs,
    plannedActions,
    emptyText,
    nowEpochSec,
    onDispatch,
    onUpdateQueue,
    onCancelQueue,
    onPinQueue
  }) => {
    const activeList = Array.isArray(execJobs) ? execJobs : [];
    const totalCount = activeList.length + plannedActions.length;
    const topSlice = plannedActions.slice(0, 10);
    return /* @__PURE__ */ jsxRuntimeExports.jsxs("article", { className: "hud-window dash-queue-card", id: cardId, children: [
      /* @__PURE__ */ jsxRuntimeExports.jsxs("div", { className: "window-bar window-bar-wrap", children: [
        /* @__PURE__ */ jsxRuntimeExports.jsxs("div", { className: "window-title-group", children: [
          /* @__PURE__ */ jsxRuntimeExports.jsx("span", { className: "module-code tabular", children: moduleCode }),
          /* @__PURE__ */ jsxRuntimeExports.jsx("h2", { children: title }),
          /* @__PURE__ */ jsxRuntimeExports.jsx("span", { className: "module-meta tabular dash-q-count", id: countId, children: String(totalCount) })
        ] }),
        /* @__PURE__ */ jsxRuntimeExports.jsxs("div", { className: "inline-actions", children: [
          /* @__PURE__ */ jsxRuntimeExports.jsx(
            "button",
            {
              type: "button",
              className: "btn btn-primary btn-sm",
              id: addBtnId,
              "aria-label": addBtnAriaLabel,
              onClick: onAddQueueClick,
              children: "+ Queue"
            }
          ),
          /* @__PURE__ */ jsxRuntimeExports.jsx(
            "button",
            {
              type: "button",
              className: "btn btn-secondary btn-sm",
              id: gotoBtnId,
              "data-goto-page": gotoPageAttr,
              onClick: onGotoClick,
              children: gotoLabel
            }
          )
        ] })
      ] }),
      /* @__PURE__ */ jsxRuntimeExports.jsx("div", { className: "table-wrap table-scroll", children: /* @__PURE__ */ jsxRuntimeExports.jsxs("table", { children: [
        /* @__PURE__ */ jsxRuntimeExports.jsx("thead", { children: /* @__PURE__ */ jsxRuntimeExports.jsxs("tr", { children: [
          /* @__PURE__ */ jsxRuntimeExports.jsx("th", { children: "Prio" }),
          /* @__PURE__ */ jsxRuntimeExports.jsx("th", { children: "Action" }),
          /* @__PURE__ */ jsxRuntimeExports.jsx("th", { children: "Missing / Cost" }),
          /* @__PURE__ */ jsxRuntimeExports.jsx("th", { className: "col-right", children: "Wait ETA" }),
          /* @__PURE__ */ jsxRuntimeExports.jsx("th", { className: "col-right", children: "Total ETA / Finish" }),
          /* @__PURE__ */ jsxRuntimeExports.jsx("th", { className: "col-right", children: "Ops" })
        ] }) }),
        /* @__PURE__ */ jsxRuntimeExports.jsx("tbody", { id: execTbodyId, className: "dash-exec-tbody", hidden: activeList.length === 0, children: activeList.map((job, idx) => /* @__PURE__ */ jsxRuntimeExports.jsx(
          ActiveExecJobRow,
          {
            job,
            nowEpochSec
          },
          `exec-${job.category || "job"}-${job.queue_slot ?? idx}-${job.name || idx}`
        )) }),
        /* @__PURE__ */ jsxRuntimeExports.jsx("tbody", { id: planTbodyId, children: topSlice.length > 0 ? topSlice.map((act, idx) => /* @__PURE__ */ jsxRuntimeExports.jsx(
          PlanActionRow,
          {
            action: act,
            compact: true,
            nowEpochSec,
            onDispatch,
            onUpdateQueue,
            onCancelQueue,
            onPinQueue
          },
          `plan-${act.manual_queue_id ?? "auto"}-${act.kind}-${act.planet_id}-${act.target_id ?? act.target_name}-${idx}`
        )) : activeList.length === 0 ? /* @__PURE__ */ jsxRuntimeExports.jsx("tr", { children: /* @__PURE__ */ jsxRuntimeExports.jsx("td", { colSpan: 6, className: "empty-cell", children: emptyText }) }) : null })
      ] }) })
    ] });
  };
  const ResourceTrackRow = ({
    idKey,
    code,
    name,
    amount,
    storage,
    hourlyRate,
    hasPlanet
  }) => {
    if (!hasPlanet) {
      return /* @__PURE__ */ jsxRuntimeExports.jsxs("div", { className: "track-row", children: [
        /* @__PURE__ */ jsxRuntimeExports.jsxs("div", { className: "track-meta", children: [
          /* @__PURE__ */ jsxRuntimeExports.jsx("span", { className: "track-code tabular", children: code }),
          /* @__PURE__ */ jsxRuntimeExports.jsx("span", { className: "track-name", children: name })
        ] }),
        /* @__PURE__ */ jsxRuntimeExports.jsx("div", { className: "track-bar-group", children: /* @__PURE__ */ jsxRuntimeExports.jsx("div", { className: "meter-track", children: /* @__PURE__ */ jsxRuntimeExports.jsx("div", { className: "meter-fill", id: `bar-${idKey}` }) }) }),
        /* @__PURE__ */ jsxRuntimeExports.jsxs("div", { className: "track-values tabular", children: [
          /* @__PURE__ */ jsxRuntimeExports.jsx("span", { className: "track-val", id: `val-${idKey}`, children: "—" }),
          /* @__PURE__ */ jsxRuntimeExports.jsx("span", { className: "track-cap", id: `cap-${idKey}`, children: "—" }),
          /* @__PURE__ */ jsxRuntimeExports.jsx("span", { className: "track-rate", id: `rate-${idKey}`, children: "—/h" })
        ] })
      ] });
    }
    const cap = finiteNum(storage, 0);
    const val = finiteNum(amount, null);
    const ratioPct = cap > 0 && val !== null ? val / cap * 100 : 0;
    const pct = Number.isFinite(ratioPct) ? Math.max(0, Math.min(100, Math.round(ratioPct))) : 0;
    const rateNum = hourlyRate == null ? null : finiteNum(hourlyRate, null);
    const rateText = hourlyRate == null ? "—/h" : fmtRate(hourlyRate);
    const rateNeg = hourlyRate != null && rateNum !== null && Math.round(rateNum) < 0;
    return /* @__PURE__ */ jsxRuntimeExports.jsxs("div", { className: "track-row", children: [
      /* @__PURE__ */ jsxRuntimeExports.jsxs("div", { className: "track-meta", children: [
        /* @__PURE__ */ jsxRuntimeExports.jsx("span", { className: "track-code tabular", children: code }),
        /* @__PURE__ */ jsxRuntimeExports.jsx("span", { className: "track-name", children: name })
      ] }),
      /* @__PURE__ */ jsxRuntimeExports.jsx("div", { className: "track-bar-group", children: /* @__PURE__ */ jsxRuntimeExports.jsx("div", { className: "meter-track", children: /* @__PURE__ */ jsxRuntimeExports.jsx(
        "div",
        {
          className: `meter-fill${pct >= 85 ? " high" : ""}`,
          id: `bar-${idKey}`,
          style: { transform: `scaleX(${pct / 100})` }
        }
      ) }) }),
      /* @__PURE__ */ jsxRuntimeExports.jsxs("div", { className: "track-values tabular", children: [
        /* @__PURE__ */ jsxRuntimeExports.jsx("span", { className: "track-val", id: `val-${idKey}`, children: fmtInt(amount ?? 0) }),
        /* @__PURE__ */ jsxRuntimeExports.jsx("span", { className: "track-cap", id: `cap-${idKey}`, children: cap > 0 ? `/ ${fmtInt(cap)} (${pct}%)` : "—" }),
        /* @__PURE__ */ jsxRuntimeExports.jsx("span", { className: `track-rate${rateNeg ? " is-neg" : ""}`, id: `rate-${idKey}`, children: rateText })
      ] })
    ] });
  };
  const DashboardView = () => {
    var _a, _b, _c, _d, _e, _f, _g, _h, _i, _j, _k, _l;
    const {
      dashboard,
      activePage,
      setActivePage,
      selectedPlanetId,
      setSelectedPlanetId,
      activePlanet,
      nowEpochSec,
      showFeedback,
      apiCommand: apiCommand2,
      loadDashboard,
      uiPrefs,
      setUiPref,
      buildingsEtaData,
      toggleBlastFurnaceRate,
      enqueueManualCommand
    } = useConsole();
    const gs = (dashboard == null ? void 0 : dashboard.state) || null;
    const planets = reactExports.useMemo(() => (gs == null ? void 0 : gs.planets) || [], [gs == null ? void 0 : gs.planets]);
    const chosenId = activePlanet == null ? void 0 : activePlanet.planet_id;
    const currentEta = reactExports.useMemo(() => {
      var _a2;
      if (buildingsEtaData && activePlanet && ((_a2 = buildingsEtaData.planet) == null ? void 0 : _a2.planet_id) === activePlanet.planet_id) {
        return buildingsEtaData;
      }
      return (dashboard == null ? void 0 : dashboard.buildings_eta) || null;
    }, [activePlanet, buildingsEtaData, dashboard == null ? void 0 : dashboard.buildings_eta]);
    const pEcon = (currentEta == null ? void 0 : currentEta.planet_economy) || null;
    const b2Pct = getSelectedBlastFurnacePct(dashboard, selectedPlanetId, buildingsEtaData);
    const prodRaw = (_a = activePlanet == null ? void 0 : activePlanet.energy) == null ? void 0 : _a.produced;
    const usedRaw = (_b = activePlanet == null ? void 0 : activePlanet.energy) == null ? void 0 : _b.used;
    const prodFinite = prodRaw == null ? 0 : finiteNum(prodRaw, null);
    const usedFinite = usedRaw == null ? 0 : finiteNum(usedRaw, null);
    const netRaw = prodFinite !== null && usedFinite !== null ? prodFinite - usedFinite : null;
    const netEnergy = netRaw === null ? null : netRaw === 0 ? 0 : netRaw;
    const energyValText = activePlanet ? netEnergy === null ? "—" : `${netEnergy >= 0 ? "+" : ""}${fmtInt(netEnergy)}` : "+0";
    const energyRatioText = activePlanet ? `${prodFinite === null ? "—" : fmtInt(prodFinite)} / ${usedFinite === null ? "—" : fmtInt(usedFinite)} E` : "0 / 0 E";
    const pTemp = finiteNum(activePlanet == null ? void 0 : activePlanet.temperature, null);
    const pFieldsUsed = finiteNum(activePlanet == null ? void 0 : activePlanet.fields_used, null);
    const pFieldsMax = finiteNum(activePlanet == null ? void 0 : activePlanet.fields_max, null);
    const pType = String((activePlanet == null ? void 0 : activePlanet.planet_type) || "normal").toUpperCase();
    const planetMetaText = activePlanet ? `${pType} · ${pTemp === null ? "—" : `${pTemp}°C`} · ${pFieldsUsed === null ? "—" : pFieldsUsed}/${pFieldsMax === null ? "—" : pFieldsMax}` : "COLONY STANDBY";
    const rnk = (dashboard == null ? void 0 : dashboard.ranking) || {};
    const curRnk = rnk.current || {};
    const rnkRates = rnk.rates || {};
    const az = curRnk.attack_zone || {};
    const rankFinite = finiteNum(curRnk.server_rank, null);
    const dashRankText = dashboard ? curRnk.server_rank != null ? rankFinite !== null ? `#${fmtInt(rankFinite)}` : "#—" : az.label || "#—" : "#—";
    const baselinePts = finiteNum(curRnk.baseline_points, null);
    const dashAzText = dashboard ? `${az.label || "AZ —"} · ${baselinePts !== null ? fmtInt(Math.round(baselinePts)) : "—"} pts` : "AZ — · — pts";
    const steadyRate = finiteNum(rnkRates.pts_per_hr_steady, 0);
    const cleanSteady = Math.abs(steadyRate) < 5e-3 || Number(steadyRate.toFixed(2)) === 0 ? 0 : steadyRate;
    const dashPtsHrText = dashboard ? `${cleanSteady >= 0 ? "+" : ""}${cleanSteady.toFixed(2)}/h` : "+0/h";
    const spiceEqRate = finiteNum(rnkRates.spice_eq_per_hr, null);
    const dashSpiceHrText = dashboard ? `${spiceEqRate !== null ? fmtInt(Math.round(spiceEqRate)) : "—"} SpiceEq/h` : "0 SpiceEq/h";
    const tut = (gs == null ? void 0 : gs.tutorial) || null;
    const incompleteTasks = reactExports.useMemo(
      () => Array.isArray(tut == null ? void 0 : tut.tasks) ? tut.tasks.filter((t) => ((t == null ? void 0 : t.current) ?? 0) < ((t == null ? void 0 : t.target) ?? 0)) : [],
      [tut]
    );
    const tutSummaryText = tut ? incompleteTasks.length > 0 ? incompleteTasks.map((t) => `${t.building_name || t.description} (${t.current}/${t.target})`).join(" · ") : `Ready to claim: ${tut.reward_text || "Step reward"}` : "Loading tutorial steps...";
    const activeQueues = reactExports.useMemo(
      () => Array.isArray(gs == null ? void 0 : gs.active_queues) ? gs.active_queues : [],
      [gs]
    );
    const effNowSec = nowEpochSec != null && nowEpochSec > 0 ? nowEpochSec : Math.floor(Date.now() / 1e3);
    const { execBuildingJobs, execFleetJobs, execResearchJobs } = reactExports.useMemo(() => {
      const bld = [];
      const flt = [];
      const res = [];
      for (const job of activeQueues) {
        const dom = dashboardQueueDomainForJob(job);
        if (dom === "research") res.push(job);
        else if (dom === "fleet") flt.push(job);
        else bld.push(job);
      }
      return { execBuildingJobs: bld, execFleetJobs: flt, execResearchJobs: res };
    }, [activeQueues]);
    const allPlanActions = reactExports.useMemo(() => (dashboard == null ? void 0 : dashboard.plan) || [], [dashboard == null ? void 0 : dashboard.plan]);
    const manualList = reactExports.useMemo(() => {
      if (Array.isArray(dashboard == null ? void 0 : dashboard.manual_queue)) return dashboard.manual_queue;
      return allPlanActions.filter(
        (a) => a.manual_queue_id != null && Number(a.manual_queue_id) > 0
      );
    }, [allPlanActions, dashboard]);
    const pinnedCount = manualList.length;
    const { buildingActions, fleetActions, researchActions } = reactExports.useMemo(() => {
      const bld = [];
      const flt = [];
      const res = [];
      for (const act of allPlanActions) {
        const dom = dashboardQueueDomainForAction(act);
        if (dom === "research") res.push(act);
        else if (dom === "fleet") flt.push(act);
        else bld.push(act);
      }
      return { buildingActions: bld, fleetActions: flt, researchActions: res };
    }, [allPlanActions]);
    const cfgMode = String(
      ((_c = dashboard == null ? void 0 : dashboard.config) == null ? void 0 : _c.planner_mode) || (dashboard == null ? void 0 : dashboard.planner_mode) || "priority"
    ).toLowerCase();
    const isGaNn = cfgMode === "ga_nn";
    const modeLabel = isGaNn ? "MODE: GA+NN" : "MODE: PRIORITY";
    const colonyCountTag = dashboard ? `${String(planets.length).padStart(2, "0")}-${String((gs == null ? void 0 : gs.universe_id) || 31)}` : "01-31";
    const matrixAccountLine = dashboard ? `CMD // ${(gs == null ? void 0 : gs.nickname) || "OPERATOR"} · :${((_d = dashboard.health) == null ? void 0 : _d.debug_port) || 9222}` : "CMD // STANDBY";
    const handleClaimTutorial = reactExports.useCallback(async () => {
      try {
        const res = await apiCommand2({ command: "claim_tutorial", dry_run: false });
        showFeedback(res.message || "Tutorial claim submitted.", "ok");
        await loadDashboard({ refresh: true, force: true });
      } catch (err) {
        showFeedback(`Tutorial claim failed: ${err.message}`, feedbackKindForError(err));
      }
    }, [apiCommand2, loadDashboard, showFeedback]);
    const openComposerWithKind = reactExports.useCallback(
      (presetKind) => {
        var _a2, _b2;
        reactDomExports.flushSync(() => {
          setUiPref("mqKind", presetKind);
          setUiPref("mqPresetSeq", Number(uiPrefs.mqPresetSeq || 0) + 1);
          setActivePage("operations");
        });
        if (typeof document !== "undefined") {
          (_b2 = (_a2 = document.getElementById("mq-kind")) == null ? void 0 : _a2.focus) == null ? void 0 : _b2.call(_a2);
        }
      },
      [setActivePage, setUiPref, uiPrefs.mqPresetSeq]
    );
    const handleGotoResearch = reactExports.useCallback(() => {
      reactDomExports.flushSync(() => {
        setUiPref("bldCategory", "research");
        setActivePage("buildings");
      });
    }, [setActivePage, setUiPref]);
    const handleDispatch = reactExports.useCallback(
      async (action, dryRun) => {
        const payload = actionPayloadFor(action, dryRun);
        if (!payload) return;
        try {
          showFeedback(
            `${dryRun ? "Simulating" : "Executing"} ${action.target_name || action.kind}...`,
            "ok"
          );
          const res = await apiCommand2(payload);
          showFeedback(res.message || `Command ${payload.command} completed.`, "ok");
          await loadDashboard({ refresh: false, force: true });
        } catch (err) {
          showFeedback(`Action failed: ${err.message}`, feedbackKindForError(err));
        }
      },
      [apiCommand2, loadDashboard, showFeedback]
    );
    const handleUpdateQueue = reactExports.useCallback(
      async (queueId, patch) => {
        try {
          const res = await apiCommand2({ command: "queue_update", queue_id: queueId, ...patch });
          showFeedback(res.message || `Updated queue item #${queueId}.`, "ok");
          await loadDashboard({ refresh: false, force: true });
        } catch (err) {
          showFeedback(`Queue update failed: ${err.message}`, feedbackKindForError(err));
        }
      },
      [apiCommand2, loadDashboard, showFeedback]
    );
    const handleCancelQueue = reactExports.useCallback(
      async (queueId) => {
        try {
          const res = await apiCommand2({ command: "queue_cancel", queue_id: queueId });
          showFeedback(res.message || `Removed queue item #${queueId}.`, "ok");
          await loadDashboard({ refresh: false, force: true });
        } catch (err) {
          showFeedback(`Cancel failed: ${err.message}`, feedbackKindForError(err));
        }
      },
      [apiCommand2, loadDashboard, showFeedback]
    );
    const handlePinQueue = reactExports.useCallback(
      (act, priority = "P1") => {
        void enqueueManualCommand(buildPinPayloadFromAction(act, priority));
      },
      [enqueueManualCommand]
    );
    return /* @__PURE__ */ jsxRuntimeExports.jsxs(
      "section",
      {
        id: "view-dashboard",
        className: "page-view dashboard-view",
        "aria-label": "Dashboard overview",
        hidden: activePage !== "dashboard",
        children: [
          /* @__PURE__ */ jsxRuntimeExports.jsxs("div", { className: "tutorial-banner", id: "tutorial-strip", hidden: !tut, children: [
            /* @__PURE__ */ jsxRuntimeExports.jsxs("div", { className: "tutorial-copy", children: [
              /* @__PURE__ */ jsxRuntimeExports.jsx("span", { className: "module-code tabular", id: "tutorial-title", children: /* @__PURE__ */ jsxRuntimeExports.jsx("span", { id: "tutorial-step-badge", children: tut ? `TUTORIAL ${tut.step}/${tut.total_steps}` : "TUTORIAL 1/11" }) }),
              /* @__PURE__ */ jsxRuntimeExports.jsx("span", { id: "tutorial-tasks-summary", className: "tabular", children: /* @__PURE__ */ jsxRuntimeExports.jsx("span", { id: "tutorial-tasks-list", children: tutSummaryText }) })
            ] }),
            /* @__PURE__ */ jsxRuntimeExports.jsx(
              "button",
              {
                type: "button",
                className: "btn btn-primary btn-sm",
                id: "btn-claim-tutorial",
                hidden: !(tut == null ? void 0 : tut.can_complete),
                onClick: handleClaimTutorial,
                children: "Claim Reward"
              }
            )
          ] }),
          /* @__PURE__ */ jsxRuntimeExports.jsxs("div", { className: "dash-upper-grid", children: [
            /* @__PURE__ */ jsxRuntimeExports.jsxs("article", { className: "hud-window", children: [
              /* @__PURE__ */ jsxRuntimeExports.jsxs("div", { className: "window-bar", children: [
                /* @__PURE__ */ jsxRuntimeExports.jsxs("div", { className: "window-title-group", children: [
                  /* @__PURE__ */ jsxRuntimeExports.jsx("span", { className: "module-code tabular", children: "SEC" }),
                  /* @__PURE__ */ jsxRuntimeExports.jsx("h2", { children: "Resources & Hourly Net Flow" })
                ] }),
                /* @__PURE__ */ jsxRuntimeExports.jsx("span", { className: "module-meta tabular", id: "command-subpath", children: /* @__PURE__ */ jsxRuntimeExports.jsx("span", { id: "planet-title", children: activePlanet ? `SECTOR // [${activePlanet.coords || "--"}] ${String(activePlanet.name || "COLONY").toUpperCase()}` : "SECTOR // ACTIVE COLONY" }) })
              ] }),
              /* @__PURE__ */ jsxRuntimeExports.jsxs("div", { className: "telemetry-core", children: [
                /* @__PURE__ */ jsxRuntimeExports.jsxs("div", { className: "track-list", "aria-label": "Planetary resource levels and net hourly flow", children: [
                  /* @__PURE__ */ jsxRuntimeExports.jsx(
                    ResourceTrackRow,
                    {
                      idKey: "pig",
                      code: "PIG",
                      name: "Pig Iron",
                      amount: (_e = activePlanet == null ? void 0 : activePlanet.resources) == null ? void 0 : _e.pig_iron,
                      storage: (_f = activePlanet == null ? void 0 : activePlanet.storage) == null ? void 0 : _f.pig_iron,
                      hourlyRate: pEcon == null ? void 0 : pEcon.pig_iron_per_hr,
                      hasPlanet: Boolean(activePlanet)
                    },
                    "pig"
                  ),
                  /* @__PURE__ */ jsxRuntimeExports.jsx(
                    ResourceTrackRow,
                    {
                      idKey: "metal",
                      code: "MET",
                      name: "Metal",
                      amount: (_g = activePlanet == null ? void 0 : activePlanet.resources) == null ? void 0 : _g.metal,
                      storage: (_h = activePlanet == null ? void 0 : activePlanet.storage) == null ? void 0 : _h.metal,
                      hourlyRate: pEcon == null ? void 0 : pEcon.metal_per_hr,
                      hasPlanet: Boolean(activePlanet)
                    },
                    "metal"
                  ),
                  /* @__PURE__ */ jsxRuntimeExports.jsx(
                    ResourceTrackRow,
                    {
                      idKey: "kryp",
                      code: "KRY",
                      name: "Kryptonite",
                      amount: (_i = activePlanet == null ? void 0 : activePlanet.resources) == null ? void 0 : _i.kryptonite,
                      storage: (_j = activePlanet == null ? void 0 : activePlanet.storage) == null ? void 0 : _j.kryptonite,
                      hourlyRate: pEcon == null ? void 0 : pEcon.kryptonite_per_hr,
                      hasPlanet: Boolean(activePlanet)
                    },
                    "kryp"
                  ),
                  /* @__PURE__ */ jsxRuntimeExports.jsx(
                    ResourceTrackRow,
                    {
                      idKey: "spice",
                      code: "SPI",
                      name: "Spice",
                      amount: (_k = activePlanet == null ? void 0 : activePlanet.resources) == null ? void 0 : _k.spice,
                      storage: (_l = activePlanet == null ? void 0 : activePlanet.storage) == null ? void 0 : _l.spice,
                      hourlyRate: pEcon == null ? void 0 : pEcon.spice_per_hr,
                      hasPlanet: Boolean(activePlanet)
                    },
                    "spice"
                  )
                ] }),
                /* @__PURE__ */ jsxRuntimeExports.jsxs("div", { className: "digital-readout-row", children: [
                  /* @__PURE__ */ jsxRuntimeExports.jsxs("div", { className: "digital-counter", children: [
                    /* @__PURE__ */ jsxRuntimeExports.jsx("span", { className: "digital-tag tabular", children: "ENG" }),
                    /* @__PURE__ */ jsxRuntimeExports.jsx("span", { className: "digital-number tabular", id: "val-energy", children: energyValText }),
                    /* @__PURE__ */ jsxRuntimeExports.jsx("span", { className: "digital-sub tabular", id: "energy-ratio", children: energyRatioText })
                  ] }),
                  /* @__PURE__ */ jsxRuntimeExports.jsxs("div", { className: "digital-counter", children: [
                    /* @__PURE__ */ jsxRuntimeExports.jsx("span", { className: "digital-tag tabular", children: "RNK" }),
                    /* @__PURE__ */ jsxRuntimeExports.jsx("span", { className: "digital-number tabular", id: "dash-rank-val", children: dashRankText }),
                    /* @__PURE__ */ jsxRuntimeExports.jsx("span", { className: "digital-sub tabular", id: "dash-az-val", children: dashAzText })
                  ] }),
                  /* @__PURE__ */ jsxRuntimeExports.jsxs("div", { className: "digital-counter", children: [
                    /* @__PURE__ */ jsxRuntimeExports.jsx("span", { className: "digital-tag tabular", children: "FLW" }),
                    /* @__PURE__ */ jsxRuntimeExports.jsx("span", { className: "digital-number tabular", id: "dash-pts-hr", children: dashPtsHrText }),
                    /* @__PURE__ */ jsxRuntimeExports.jsx("span", { className: "digital-sub tabular", id: "dash-spice-hr", children: dashSpiceHrText })
                  ] })
                ] })
              ] })
            ] }),
            /* @__PURE__ */ jsxRuntimeExports.jsxs("article", { className: "hud-window", children: [
              /* @__PURE__ */ jsxRuntimeExports.jsxs("div", { className: "window-bar window-bar-wrap", children: [
                /* @__PURE__ */ jsxRuntimeExports.jsxs("div", { className: "window-title-group", children: [
                  /* @__PURE__ */ jsxRuntimeExports.jsx("span", { className: "module-code tabular", children: "MAP" }),
                  /* @__PURE__ */ jsxRuntimeExports.jsx("h2", { children: "Empire Colonies & Active Queues" }),
                  /* @__PURE__ */ jsxRuntimeExports.jsx("span", { className: "module-meta tabular", id: "planet-meta", children: planetMetaText })
                ] }),
                /* @__PURE__ */ jsxRuntimeExports.jsxs("div", { className: "inline-actions", children: [
                  /* @__PURE__ */ jsxRuntimeExports.jsx("span", { className: "module-meta tabular", id: "econ-bf-status", children: b2Pct === 0 ? "B2: 0%" : `B2: ${b2Pct}%` }),
                  /* @__PURE__ */ jsxRuntimeExports.jsx(
                    "button",
                    {
                      type: "button",
                      className: b2Pct === 0 ? "btn btn-primary btn-sm" : "btn btn-secondary btn-sm",
                      id: "btn-dash-b2-toggle",
                      "data-target-pct": b2Pct === 0 ? "100" : "0",
                      onClick: () => toggleBlastFurnaceRate(b2Pct === 0 ? 100 : 0),
                      children: b2Pct === 0 ? "Resume b2 (100%)" : "Pause b2 (0% +Pig)"
                    }
                  ),
                  /* @__PURE__ */ jsxRuntimeExports.jsx("span", { className: "module-meta tabular", id: "colony-count-tag", children: colonyCountTag })
                ] })
              ] }),
              /* @__PURE__ */ jsxRuntimeExports.jsxs("div", { className: "matrix-body", children: [
                /* @__PURE__ */ jsxRuntimeExports.jsx("div", { className: "table-wrap table-scroll", children: /* @__PURE__ */ jsxRuntimeExports.jsxs("table", { children: [
                  /* @__PURE__ */ jsxRuntimeExports.jsx("thead", { children: /* @__PURE__ */ jsxRuntimeExports.jsxs("tr", { children: [
                    /* @__PURE__ */ jsxRuntimeExports.jsx("th", { children: "Coords" }),
                    /* @__PURE__ */ jsxRuntimeExports.jsx("th", { children: "Colony" }),
                    /* @__PURE__ */ jsxRuntimeExports.jsx("th", { children: "State" }),
                    /* @__PURE__ */ jsxRuntimeExports.jsx("th", { className: "col-right", children: "Fields" })
                  ] }) }),
                  /* @__PURE__ */ jsxRuntimeExports.jsx("tbody", { id: "colony-tbody", children: planets.length === 0 ? /* @__PURE__ */ jsxRuntimeExports.jsx("tr", { children: /* @__PURE__ */ jsxRuntimeExports.jsx("td", { colSpan: 4, className: "empty-cell", children: dashboard ? "No colonies discovered." : "Waiting for snapshot..." }) }) : planets.map((p) => {
                    var _a2, _b2, _c2, _d2;
                    const isActive = p.planet_id === chosenId;
                    const pigStor = finiteNum((_a2 = p.storage) == null ? void 0 : _a2.pig_iron, 0);
                    const pigAmt = finiteNum((_b2 = p.resources) == null ? void 0 : _b2.pig_iron, 0);
                    const spiStor = finiteNum((_c2 = p.storage) == null ? void 0 : _c2.spice, 0);
                    const spiAmt = finiteNum((_d2 = p.resources) == null ? void 0 : _d2.spice, 0);
                    const nearFull = pigStor > 0 && pigAmt / pigStor >= 0.85 || spiStor > 0 && spiAmt / spiStor >= 0.85;
                    const statusWord = nearFull ? "NEAR-CAP" : isActive ? "ONLINE" : "READY";
                    const fUsed = finiteNum(p.fields_used, null);
                    const fMax = finiteNum(p.fields_max, null);
                    return /* @__PURE__ */ jsxRuntimeExports.jsxs(
                      "tr",
                      {
                        className: `clickable-row${isActive ? " is-active" : ""}`,
                        onClick: () => setSelectedPlanetId(p.planet_id),
                        children: [
                          /* @__PURE__ */ jsxRuntimeExports.jsx("td", { className: "tabular", children: p.coords || "--" }),
                          /* @__PURE__ */ jsxRuntimeExports.jsx("td", { children: p.name || "Colony" }),
                          /* @__PURE__ */ jsxRuntimeExports.jsx("td", { children: /* @__PURE__ */ jsxRuntimeExports.jsx("span", { className: `status-word${nearFull ? " is-warn" : ""}`, children: statusWord }) }),
                          /* @__PURE__ */ jsxRuntimeExports.jsx("td", { className: "col-right tabular", children: `${fUsed === null ? "—" : fUsed}/${fMax === null ? "—" : fMax}` })
                        ]
                      },
                      p.planet_id
                    );
                  }) })
                ] }) }),
                /* @__PURE__ */ jsxRuntimeExports.jsxs("div", { className: "queue-strip", children: [
                  /* @__PURE__ */ jsxRuntimeExports.jsxs("div", { className: "queue-strip-head tabular", children: [
                    /* @__PURE__ */ jsxRuntimeExports.jsx("span", { children: "ACTIVE QUEUES" }),
                    /* @__PURE__ */ jsxRuntimeExports.jsx("span", { id: "matrix-account-line", children: matrixAccountLine })
                  ] }),
                  /* @__PURE__ */ jsxRuntimeExports.jsx("div", { id: "active-queues-list", className: "queue-summary tabular", children: activeQueues.length === 0 ? dashboard ? "No active construction or research jobs." : "No active construction jobs." : activeQueues.map((q, idx) => {
                    const catTag = `[${String(q.category || "JOB").toUpperCase()}]`;
                    const titleTxt = cleanQueueJobTitle(q);
                    const finTs = finiteNum(q.finish_ts, null);
                    return /* @__PURE__ */ jsxRuntimeExports.jsxs(React.Fragment, { children: [
                      idx > 0 ? " · " : null,
                      /* @__PURE__ */ jsxRuntimeExports.jsx("span", { children: finTs !== null && finTs > 0 ? /* @__PURE__ */ jsxRuntimeExports.jsxs(jsxRuntimeExports.Fragment, { children: [
                        `${catTag} ${titleTxt} (`,
                        /* @__PURE__ */ jsxRuntimeExports.jsx("span", { "data-finish-ts": String(finTs), children: fmtHms(Math.max(0, Math.round(finTs - effNowSec))) }),
                        ` · ${fmtClockTime(finTs, effNowSec)})`
                      ] }) : q.remaining_text ? `${catTag} ${titleTxt} (${q.remaining_text})` : `${catTag} ${titleTxt}` })
                    ] }, `qstrip-${idx}-${q.name || idx}`);
                  }) })
                ] })
              ] })
            ] })
          ] }),
          /* @__PURE__ */ jsxRuntimeExports.jsx(GaNnScannerHud, {}),
          /* @__PURE__ */ jsxRuntimeExports.jsxs("div", { className: "dash-queues-toolbar", "aria-label": "Planned queues overview and navigation", children: [
            /* @__PURE__ */ jsxRuntimeExports.jsxs("div", { className: "window-title-group", children: [
              /* @__PURE__ */ jsxRuntimeExports.jsx(
                "span",
                {
                  className: `module-meta tabular mode-badge${isGaNn ? " is-ga" : ""}`,
                  id: "dash-planner-mode-badge",
                  children: modeLabel
                }
              ),
              /* @__PURE__ */ jsxRuntimeExports.jsx(
                "span",
                {
                  className: "module-meta tabular",
                  id: "dash-pinned-count-badge",
                  hidden: pinnedCount === 0,
                  children: `${pinnedCount} PINNED`
                }
              )
            ] }),
            /* @__PURE__ */ jsxRuntimeExports.jsxs("div", { className: "inline-actions", children: [
              /* @__PURE__ */ jsxRuntimeExports.jsx(
                "button",
                {
                  type: "button",
                  className: "btn btn-secondary btn-sm",
                  "data-goto-page": "action-graph",
                  onClick: () => setActivePage("action-graph"),
                  children: "Action Graph →"
                }
              ),
              /* @__PURE__ */ jsxRuntimeExports.jsx(
                "button",
                {
                  type: "button",
                  className: "btn btn-secondary btn-sm",
                  "data-goto-page": "operations",
                  onClick: () => setActivePage("operations"),
                  children: "Full Queue →"
                }
              )
            ] })
          ] }),
          /* @__PURE__ */ jsxRuntimeExports.jsxs("div", { className: "dash-lower-grid", children: [
            /* @__PURE__ */ jsxRuntimeExports.jsx(
              DashboardQueueCard,
              {
                cardId: "dash-queue-card-building",
                moduleCode: "NXQ",
                title: "Building Queue",
                countId: "dash-bld-q-count",
                addBtnId: "btn-dash-add-queue",
                addBtnAriaLabel: "Queue building command",
                onAddQueueClick: () => openComposerWithKind("BUILD"),
                gotoLabel: "Buildings →",
                gotoPageAttr: "buildings",
                onGotoClick: () => setActivePage("buildings"),
                execTbodyId: "dash-exec-building-tbody",
                planTbodyId: "dash-plan-tbody",
                execJobs: execBuildingJobs,
                plannedActions: buildingActions,
                emptyText: dashboard ? "No queued building actions." : "Run Sync to populate building actions...",
                nowEpochSec: effNowSec,
                onDispatch: handleDispatch,
                onUpdateQueue: handleUpdateQueue,
                onCancelQueue: handleCancelQueue,
                onPinQueue: handlePinQueue
              }
            ),
            /* @__PURE__ */ jsxRuntimeExports.jsx(
              DashboardQueueCard,
              {
                cardId: "dash-queue-card-fleet",
                moduleCode: "FLT",
                title: "Fleet Queue",
                countId: "dash-fleet-q-count",
                addBtnId: "btn-dash-add-fleet-queue",
                addBtnAriaLabel: "Queue fleet or ship command",
                onAddQueueClick: () => openComposerWithKind("BUILD_SHIP"),
                gotoLabel: "Fleet →",
                gotoPageAttr: "combat",
                onGotoClick: () => setActivePage("combat"),
                execTbodyId: "dash-exec-fleet-tbody",
                planTbodyId: "dash-plan-fleet-tbody",
                execJobs: execFleetJobs,
                plannedActions: fleetActions,
                emptyText: "No queued fleet or ship actions.",
                nowEpochSec: effNowSec,
                onDispatch: handleDispatch,
                onUpdateQueue: handleUpdateQueue,
                onCancelQueue: handleCancelQueue,
                onPinQueue: handlePinQueue
              }
            ),
            /* @__PURE__ */ jsxRuntimeExports.jsx(
              DashboardQueueCard,
              {
                cardId: "dash-queue-card-research",
                moduleCode: "RES",
                title: "Research Queue",
                countId: "dash-research-q-count",
                addBtnId: "btn-dash-add-res-queue",
                addBtnAriaLabel: "Queue research command",
                onAddQueueClick: () => openComposerWithKind("RESEARCH"),
                gotoLabel: "Research →",
                gotoBtnId: "btn-dash-goto-research",
                onGotoClick: handleGotoResearch,
                execTbodyId: "dash-exec-research-tbody",
                planTbodyId: "dash-plan-research-tbody",
                execJobs: execResearchJobs,
                plannedActions: researchActions,
                emptyText: "No queued research actions.",
                nowEpochSec: effNowSec,
                onDispatch: handleDispatch,
                onUpdateQueue: handleUpdateQueue,
                onCancelQueue: handleCancelQueue,
                onPinQueue: handlePinQueue
              }
            )
          ] })
        ]
      }
    );
  };
  function resolveDefaultTargetId(kind, prevTargetId) {
    const normKind = String(kind || "BUILD").toUpperCase();
    if (normKind === "BUILD" || normKind === "DEMOLISH_BUILDING" || normKind === "THROTTLE_BUILDING") {
      const items = normKind === "THROTTLE_BUILDING" ? MQ_BUILDINGS_CATALOG.slice(0, 6) : MQ_BUILDINGS_CATALOG;
      if (items.some((b) => String(b.id) === prevTargetId)) {
        return prevTargetId;
      }
      if (normKind === "DEMOLISH_BUILDING") return "6";
      if (normKind === "THROTTLE_BUILDING") return "2";
      return "1";
    }
    if (normKind === "RESEARCH") {
      return MQ_RESEARCH_CATALOG.some((r) => String(r.id) === prevTargetId) ? prevTargetId : "1";
    }
    if (normKind === "BUILD_SHIP") {
      return MQ_SHIPS_CATALOG.some((s) => String(s.id) === prevTargetId) ? prevTargetId : "1";
    }
    return prevTargetId || "1";
  }
  function computeDefaultLevelValue(kind, targetId, chosenPlanet, researches) {
    const normKind = String(kind || "BUILD").toUpperCase();
    const bMap = (chosenPlanet == null ? void 0 : chosenPlanet.buildings) || {};
    if (normKind === "BUILD") {
      const bid = Number(targetId || 1);
      const curL = finiteNum(bMap[`b${bid}`], 0);
      return String(curL + 1);
    }
    if (normKind === "DEMOLISH_BUILDING") {
      const bid = Number(targetId || 6);
      const curL = finiteNum(bMap[`b${bid}`], 0);
      return String(Math.max(0, curL - 1));
    }
    if (normKind === "THROTTLE_BUILDING") {
      return "100";
    }
    if (normKind === "RESEARCH") {
      const rid = Number(targetId || 1);
      const curL = finiteNum(researches[`r${rid}`], 0);
      return String(curL + 1);
    }
    if (normKind === "BUILD_SHIP" || normKind === "SPY" || normKind === "RECYCLE") {
      return "1";
    }
    return "";
  }
  const ManualQueueComposer = () => {
    var _a, _b, _c;
    const { dashboard, activePlanet, showFeedback, enqueueManualCommand, uiPrefs } = useConsole();
    const planets = reactExports.useMemo(() => {
      var _a2;
      return ((_a2 = dashboard == null ? void 0 : dashboard.state) == null ? void 0 : _a2.planets) || [];
    }, [(_a = dashboard == null ? void 0 : dashboard.state) == null ? void 0 : _a.planets]);
    const researches = reactExports.useMemo(
      () => {
        var _a2;
        return ((_a2 = dashboard == null ? void 0 : dashboard.state) == null ? void 0 : _a2.researches) || {};
      },
      [(_b = dashboard == null ? void 0 : dashboard.state) == null ? void 0 : _b.researches]
    );
    const presetKind = String(uiPrefs.mqKind || "BUILD").toUpperCase();
    const presetSeq = Number(uiPrefs.mqPresetSeq || 0);
    const [kind, setKind] = reactExports.useState(presetKind);
    const [planetId, setPlanetId] = reactExports.useState(() => (activePlanet == null ? void 0 : activePlanet.planet_id) ?? 0);
    const [targetId, setTargetId] = reactExports.useState(() => resolveDefaultTargetId(presetKind, "1"));
    const [lastPresetSeq, setLastPresetSeq] = reactExports.useState(presetSeq);
    const [hydratedPlanetOnce, setHydratedPlanetOnce] = reactExports.useState(false);
    const formRef = reactExports.useRef(null);
    const planetSelectRef = reactExports.useRef(null);
    const kindSelectRef = reactExports.useRef(null);
    const targetSelectRef = reactExports.useRef(null);
    const coordsInputRef = reactExports.useRef(null);
    const levelInputRef = reactExports.useRef(null);
    const prioSelectRef = reactExports.useRef(null);
    const posSelectRef = reactExports.useRef(null);
    const noteInputRef = reactExports.useRef(null);
    const pendingForceLevelSyncRef = reactExports.useRef(true);
    const handledEventsRef = reactExports.useRef(/* @__PURE__ */ new WeakSet());
    if (presetSeq !== lastPresetSeq) {
      setLastPresetSeq(presetSeq);
      const nextTarget = resolveDefaultTargetId(presetKind, targetId);
      setKind(presetKind);
      setTargetId(nextTarget);
      if (activePlanet == null ? void 0 : activePlanet.planet_id) {
        setPlanetId(activePlanet.planet_id);
      }
      pendingForceLevelSyncRef.current = true;
    }
    if (planets.length > 0 && (!hydratedPlanetOnce || !planets.some((p) => p.planet_id === planetId))) {
      const fallbackPid = (activePlanet == null ? void 0 : activePlanet.planet_id) ?? planets[0].planet_id;
      if (planetId !== fallbackPid || !hydratedPlanetOnce) {
        setHydratedPlanetOnce(true);
        setPlanetId(fallbackPid);
        pendingForceLevelSyncRef.current = true;
      }
    }
    const chosenPlanet = reactExports.useMemo(() => {
      if (planets.length === 0) return activePlanet;
      return planets.find((p) => p.planet_id === planetId) || activePlanet || planets[0] || null;
    }, [activePlanet, planetId, planets]);
    const bMap = (chosenPlanet == null ? void 0 : chosenPlanet.buildings) || {};
    const sMap = (chosenPlanet == null ? void 0 : chosenPlanet.ships) || {};
    const raidCoordsList = reactExports.useMemo(() => {
      var _a2;
      const raw = (_a2 = dashboard == null ? void 0 : dashboard.state) == null ? void 0 : _a2.raid_targets;
      if (!Array.isArray(raw)) return [];
      const seen = /* @__PURE__ */ new Set();
      for (const rt of raw) {
        const c = (rt == null ? void 0 : rt.coords) || (rt == null ? void 0 : rt.target_coords);
        if (c) seen.add(String(c));
        if (seen.size >= 40) break;
      }
      return Array.from(seen);
    }, [(_c = dashboard == null ? void 0 : dashboard.state) == null ? void 0 : _c.raid_targets]);
    const normKind = String(kind || "BUILD").toUpperCase();
    const isBuildingKind = normKind === "BUILD" || normKind === "DEMOLISH_BUILDING" || normKind === "THROTTLE_BUILDING";
    const isResearchKind = normKind === "RESEARCH";
    const isShipKind = normKind === "BUILD_SHIP";
    const isFleetCoordsKind = normKind === "SPY" || normKind === "ATTACK" || normKind === "RECYCLE" || normKind === "COLONIZE";
    const isScanKind = normKind === "SCAN_GALAXY";
    const showUnitWrap = isBuildingKind || isResearchKind || isShipKind;
    const showCoordsWrap = isFleetCoordsKind || isScanKind;
    const showLevelWrap = isBuildingKind || isResearchKind || isShipKind || normKind === "SPY" || normKind === "RECYCLE";
    const unitLabel = isBuildingKind ? "Building" : isResearchKind ? "Research" : isShipKind ? "Ship Class" : "Target Unit";
    const coordsLabel = isScanKind ? "System (GG:SSS)" : "Target Coords";
    const defSys = (chosenPlanet == null ? void 0 : chosenPlanet.coords) ? chosenPlanet.coords.slice(0, 6) : "02:380";
    const coordsPlaceholder = isScanKind ? defSys : "02:381:12";
    let levelLabel = "Goal Lvl";
    let levelMin = "1";
    let levelMax = "200";
    let levelStep = "1";
    if (normKind === "DEMOLISH_BUILDING") {
      levelLabel = "Down To Lvl";
      levelMin = "0";
      levelMax = "200";
    } else if (normKind === "THROTTLE_BUILDING") {
      levelLabel = "Rate %";
      levelMin = "0";
      levelMax = "100";
      levelStep = "10";
    } else if (isShipKind) {
      levelLabel = "Quantity";
      levelMin = "1";
      levelMax = "99999";
    } else if (normKind === "SPY") {
      levelLabel = "Probes";
      levelMin = "1";
      levelMax = "9999";
    } else if (normKind === "RECYCLE") {
      levelLabel = "Recyclers";
      levelMin = "1";
      levelMax = "9999";
    }
    const unitOptions = reactExports.useMemo(() => {
      if (isBuildingKind) {
        const items = normKind === "THROTTLE_BUILDING" ? MQ_BUILDINGS_CATALOG.slice(0, 6) : MQ_BUILDINGS_CATALOG;
        return items.map((b) => ({
          value: String(b.id),
          label: `${b.name} (L${finiteNum(bMap[`b${b.id}`], 0)})`
        }));
      }
      if (isResearchKind) {
        return MQ_RESEARCH_CATALOG.map((r) => ({
          value: String(r.id),
          label: `${r.name} (L${finiteNum(researches[`r${r.id}`], 0)})`
        }));
      }
      if (isShipKind) {
        return MQ_SHIPS_CATALOG.map((s) => ({
          value: String(s.id),
          label: `${s.name} (${finiteNum(sMap[s.key], 0)} owned)`
        }));
      }
      return [];
    }, [bMap, isBuildingKind, isResearchKind, isShipKind, normKind, researches, sMap]);
    const defaultLevelStr = reactExports.useMemo(
      () => computeDefaultLevelValue(normKind, targetId, chosenPlanet, researches),
      [chosenPlanet, normKind, researches, targetId]
    );
    reactExports.useEffect(() => {
      const lvlInp = levelInputRef.current;
      if (lvlInp) {
        const activeEl = typeof document !== "undefined" ? document.activeElement : null;
        const isEditingLevel = activeEl === lvlInp;
        if (pendingForceLevelSyncRef.current || !isEditingLevel && !lvlInp.value) {
          lvlInp.value = defaultLevelStr;
        }
      }
      if (normKind === "SCAN_GALAXY" && coordsInputRef.current && pendingForceLevelSyncRef.current) {
        if (!coordsInputRef.current.value) {
          coordsInputRef.current.value = defSys;
        }
      }
      pendingForceLevelSyncRef.current = false;
    }, [defaultLevelStr, defSys, normKind, planetId, presetSeq, targetId]);
    const applyKindChange = reactExports.useCallback(
      (rawKind) => {
        var _a2;
        const nextKind = String(rawKind || "BUILD").toUpperCase();
        const nextTarget = resolveDefaultTargetId(nextKind, ((_a2 = targetSelectRef.current) == null ? void 0 : _a2.value) || targetId);
        setKind(nextKind);
        setTargetId(nextTarget);
        if (levelInputRef.current) {
          levelInputRef.current.value = computeDefaultLevelValue(
            nextKind,
            nextTarget,
            chosenPlanet,
            researches
          );
        }
        if (nextKind === "SCAN_GALAXY" && coordsInputRef.current && !coordsInputRef.current.value) {
          coordsInputRef.current.value = defSys;
        }
      },
      [chosenPlanet, defSys, researches, targetId]
    );
    const applyPlanetChange = reactExports.useCallback(
      (rawPid) => {
        const nextPid = Math.max(0, Math.round(finiteNum(rawPid, 0)));
        setPlanetId(nextPid);
        const nextPlanet = planets.find((p) => p.planet_id === nextPid) || chosenPlanet;
        if (levelInputRef.current) {
          levelInputRef.current.value = computeDefaultLevelValue(
            normKind,
            targetId,
            nextPlanet,
            researches
          );
        }
      },
      [chosenPlanet, normKind, planets, researches, targetId]
    );
    const applyTargetChange = reactExports.useCallback(
      (rawTid) => {
        const nextTid = String(rawTid || "1");
        setTargetId(nextTid);
        if (levelInputRef.current) {
          levelInputRef.current.value = computeDefaultLevelValue(
            normKind,
            nextTid,
            chosenPlanet,
            researches
          );
        }
      },
      [chosenPlanet, normKind, researches]
    );
    const submitForm = reactExports.useCallback(async () => {
      var _a2, _b2, _c2, _d, _e, _f, _g, _h, _i, _j, _k;
      const curKind = String(((_a2 = kindSelectRef.current) == null ? void 0 : _a2.value) || normKind || "BUILD").toUpperCase();
      const curPriority = String(((_b2 = prioSelectRef.current) == null ? void 0 : _b2.value) || "P1").toUpperCase();
      const curPosition = String(((_c2 = posSelectRef.current) == null ? void 0 : _c2.value) || "top").toLowerCase();
      const curPlanetId = Math.max(
        0,
        Math.round(finiteNum(((_d = planetSelectRef.current) == null ? void 0 : _d.value) ?? planetId, 0))
      );
      const curNote = String(((_e = noteInputRef.current) == null ? void 0 : _e.value) || "").trim();
      const payload = {
        kind: curKind,
        priority: curPriority,
        position: curPosition,
        planet_id: curPlanetId,
        note: curNote
      };
      if (["BUILD", "DEMOLISH_BUILDING", "RESEARCH", "BUILD_SHIP", "THROTTLE_BUILDING"].includes(
        curKind
      )) {
        payload.target_id = Math.max(
          1,
          Math.round(finiteNum(((_f = targetSelectRef.current) == null ? void 0 : _f.value) ?? targetId, 1))
        );
        const rawLvl = String(((_g = levelInputRef.current) == null ? void 0 : _g.value) ?? "").trim();
        if (rawLvl !== "") {
          payload.target_level = Math.max(0, Math.round(finiteNum(rawLvl, 1)));
        }
      } else if (["SPY", "ATTACK", "RECYCLE", "COLONIZE", "SCAN_GALAXY"].includes(curKind)) {
        const coords = String(((_h = coordsInputRef.current) == null ? void 0 : _h.value) || "").trim();
        if (!coords) {
          showFeedback("Enter target coordinates (e.g. 02:381:12) before queuing.", "warn");
          (_j = (_i = coordsInputRef.current) == null ? void 0 : _i.focus) == null ? void 0 : _j.call(_i);
          return;
        }
        payload.target_coords = coords;
        if (curKind === "SPY" || curKind === "RECYCLE") {
          payload.target_level = Math.max(1, Math.round(finiteNum((_k = levelInputRef.current) == null ? void 0 : _k.value, 1)));
        }
      }
      const res = await enqueueManualCommand(payload);
      if (res && noteInputRef.current) {
        noteInputRef.current.value = "";
      }
    }, [enqueueManualCommand, normKind, planetId, showFeedback, targetId]);
    reactExports.useEffect(() => {
      const kSel = kindSelectRef.current;
      const pSel = planetSelectRef.current;
      const tSel = targetSelectRef.current;
      const form = formRef.current;
      const onKindNative = (ev) => {
        if (handledEventsRef.current.has(ev)) return;
        handledEventsRef.current.add(ev);
        if (kSel) reactDomExports.flushSync(() => applyKindChange(kSel.value));
      };
      const onPlanetNative = (ev) => {
        if (handledEventsRef.current.has(ev)) return;
        handledEventsRef.current.add(ev);
        if (pSel) reactDomExports.flushSync(() => applyPlanetChange(pSel.value));
      };
      const onTargetNative = (ev) => {
        if (handledEventsRef.current.has(ev)) return;
        handledEventsRef.current.add(ev);
        if (tSel) reactDomExports.flushSync(() => applyTargetChange(tSel.value));
      };
      const onFormNative = (ev) => {
        var _a2;
        (_a2 = ev.preventDefault) == null ? void 0 : _a2.call(ev);
        if (handledEventsRef.current.has(ev)) return;
        handledEventsRef.current.add(ev);
        void submitForm();
      };
      kSel == null ? void 0 : kSel.addEventListener("change", onKindNative);
      pSel == null ? void 0 : pSel.addEventListener("change", onPlanetNative);
      tSel == null ? void 0 : tSel.addEventListener("change", onTargetNative);
      form == null ? void 0 : form.addEventListener("submit", onFormNative);
      return () => {
        kSel == null ? void 0 : kSel.removeEventListener("change", onKindNative);
        pSel == null ? void 0 : pSel.removeEventListener("change", onPlanetNative);
        tSel == null ? void 0 : tSel.removeEventListener("change", onTargetNative);
        form == null ? void 0 : form.removeEventListener("submit", onFormNative);
      };
    }, [applyKindChange, applyPlanetChange, applyTargetChange, submitForm]);
    return /* @__PURE__ */ jsxRuntimeExports.jsxs(
      "form",
      {
        ref: formRef,
        className: "mq-composer-bar",
        id: "mq-composer-form",
        "aria-label": "Enqueue prioritized command",
        onSubmit: (e) => {
          e.preventDefault();
          if (handledEventsRef.current.has(e.nativeEvent)) return;
          handledEventsRef.current.add(e.nativeEvent);
          void submitForm();
        },
        children: [
          /* @__PURE__ */ jsxRuntimeExports.jsxs("div", { className: "mq-field", children: [
            /* @__PURE__ */ jsxRuntimeExports.jsx("label", { htmlFor: "mq-planet", children: "Colony" }),
            /* @__PURE__ */ jsxRuntimeExports.jsx(
              "select",
              {
                ref: planetSelectRef,
                id: "mq-planet",
                value: planets.length > 0 ? String((chosenPlanet == null ? void 0 : chosenPlanet.planet_id) ?? planetId) : "0",
                onChange: (e) => {
                  handledEventsRef.current.add(e.nativeEvent);
                  applyPlanetChange(e.target.value);
                },
                children: planets.length === 0 ? /* @__PURE__ */ jsxRuntimeExports.jsx("option", { value: "0", children: "Active Colony" }) : planets.map((p) => /* @__PURE__ */ jsxRuntimeExports.jsx("option", { value: String(p.planet_id), children: `[${p.coords}] ${p.name || "Colony"}` }, p.planet_id))
              }
            )
          ] }),
          /* @__PURE__ */ jsxRuntimeExports.jsxs("div", { className: "mq-field", children: [
            /* @__PURE__ */ jsxRuntimeExports.jsx("label", { htmlFor: "mq-kind", children: "Command" }),
            /* @__PURE__ */ jsxRuntimeExports.jsxs(
              "select",
              {
                ref: kindSelectRef,
                id: "mq-kind",
                value: normKind,
                onChange: (e) => {
                  handledEventsRef.current.add(e.nativeEvent);
                  applyKindChange(e.target.value);
                },
                children: [
                  /* @__PURE__ */ jsxRuntimeExports.jsx("option", { value: "BUILD", children: "Build / Upgrade Building" }),
                  /* @__PURE__ */ jsxRuntimeExports.jsx("option", { value: "DEMOLISH_BUILDING", children: "Demolish Building" }),
                  /* @__PURE__ */ jsxRuntimeExports.jsx("option", { value: "RESEARCH", children: "Start Research" }),
                  /* @__PURE__ */ jsxRuntimeExports.jsx("option", { value: "BUILD_SHIP", children: "Build Ships" }),
                  /* @__PURE__ */ jsxRuntimeExports.jsx("option", { value: "THROTTLE_BUILDING", children: "Throttle Building %" }),
                  /* @__PURE__ */ jsxRuntimeExports.jsx("option", { value: "SPY", children: "Spy Target" }),
                  /* @__PURE__ */ jsxRuntimeExports.jsx("option", { value: "ATTACK", children: "Attack / Raid Target" }),
                  /* @__PURE__ */ jsxRuntimeExports.jsx("option", { value: "RECYCLE", children: "Recycle Debris" }),
                  /* @__PURE__ */ jsxRuntimeExports.jsx("option", { value: "COLONIZE", children: "Colonize Free Slot" }),
                  /* @__PURE__ */ jsxRuntimeExports.jsx("option", { value: "SCAN_GALAXY", children: "Scan Galaxy System" }),
                  /* @__PURE__ */ jsxRuntimeExports.jsx("option", { value: "CLAIM_TUTORIAL", children: "Claim Tutorial Reward" })
                ]
              }
            )
          ] }),
          /* @__PURE__ */ jsxRuntimeExports.jsxs("div", { className: "mq-field mq-field-wide", id: "mq-unit-wrap", hidden: !showUnitWrap, children: [
            /* @__PURE__ */ jsxRuntimeExports.jsx("label", { htmlFor: "mq-target-id", id: "mq-unit-label", children: unitLabel }),
            /* @__PURE__ */ jsxRuntimeExports.jsx(
              "select",
              {
                ref: targetSelectRef,
                id: "mq-target-id",
                value: targetId,
                onChange: (e) => {
                  handledEventsRef.current.add(e.nativeEvent);
                  applyTargetChange(e.target.value);
                },
                children: unitOptions.map((opt) => /* @__PURE__ */ jsxRuntimeExports.jsx("option", { value: opt.value, children: opt.label }, opt.value))
              }
            )
          ] }),
          /* @__PURE__ */ jsxRuntimeExports.jsxs("div", { className: "mq-field", id: "mq-coords-wrap", hidden: !showCoordsWrap, children: [
            /* @__PURE__ */ jsxRuntimeExports.jsx("label", { htmlFor: "mq-coords", id: "mq-coords-label", children: coordsLabel }),
            /* @__PURE__ */ jsxRuntimeExports.jsx(
              "input",
              {
                ref: coordsInputRef,
                type: "text",
                id: "mq-coords",
                list: "mq-coords-datalist",
                placeholder: coordsPlaceholder,
                defaultValue: "",
                autoComplete: "off"
              }
            ),
            /* @__PURE__ */ jsxRuntimeExports.jsx("datalist", { id: "mq-coords-datalist", children: raidCoordsList.map((c) => /* @__PURE__ */ jsxRuntimeExports.jsx("option", { value: c }, c)) })
          ] }),
          /* @__PURE__ */ jsxRuntimeExports.jsxs("div", { className: "mq-field mq-field-num", id: "mq-level-wrap", hidden: !showLevelWrap, children: [
            /* @__PURE__ */ jsxRuntimeExports.jsx("label", { htmlFor: "mq-level", id: "mq-level-label", children: levelLabel }),
            /* @__PURE__ */ jsxRuntimeExports.jsx(
              "input",
              {
                ref: levelInputRef,
                type: "number",
                id: "mq-level",
                min: levelMin,
                max: levelMax,
                step: levelStep,
                defaultValue: defaultLevelStr,
                placeholder: "Auto"
              }
            )
          ] }),
          /* @__PURE__ */ jsxRuntimeExports.jsxs("div", { className: "mq-field mq-field-prio", children: [
            /* @__PURE__ */ jsxRuntimeExports.jsx("label", { htmlFor: "mq-priority", children: "Priority" }),
            /* @__PURE__ */ jsxRuntimeExports.jsxs("select", { ref: prioSelectRef, id: "mq-priority", defaultValue: "P1", children: [
              /* @__PURE__ */ jsxRuntimeExports.jsx("option", { value: "P0", children: "P0 · Immediate" }),
              /* @__PURE__ */ jsxRuntimeExports.jsx("option", { value: "P1", children: "P1 · High" }),
              /* @__PURE__ */ jsxRuntimeExports.jsx("option", { value: "P2", children: "P2 · Normal" }),
              /* @__PURE__ */ jsxRuntimeExports.jsx("option", { value: "P3", children: "P3 · Low" })
            ] })
          ] }),
          /* @__PURE__ */ jsxRuntimeExports.jsxs("div", { className: "mq-field mq-field-pos", children: [
            /* @__PURE__ */ jsxRuntimeExports.jsx("label", { htmlFor: "mq-position", children: "Order" }),
            /* @__PURE__ */ jsxRuntimeExports.jsxs("select", { ref: posSelectRef, id: "mq-position", defaultValue: "top", children: [
              /* @__PURE__ */ jsxRuntimeExports.jsx("option", { value: "top", children: "Top of Band" }),
              /* @__PURE__ */ jsxRuntimeExports.jsx("option", { value: "bottom", children: "End of Band" })
            ] })
          ] }),
          /* @__PURE__ */ jsxRuntimeExports.jsxs("div", { className: "mq-field mq-field-note", children: [
            /* @__PURE__ */ jsxRuntimeExports.jsx("label", { htmlFor: "mq-note", children: "Note" }),
            /* @__PURE__ */ jsxRuntimeExports.jsx(
              "input",
              {
                ref: noteInputRef,
                type: "text",
                id: "mq-note",
                maxLength: 140,
                defaultValue: "",
                placeholder: "Optional note"
              }
            )
          ] }),
          /* @__PURE__ */ jsxRuntimeExports.jsx("div", { className: "mq-submit-wrap", children: /* @__PURE__ */ jsxRuntimeExports.jsx("button", { type: "submit", className: "btn btn-primary btn-sm", id: "btn-mq-submit", children: "+ Enqueue" }) })
        ]
      }
    );
  };
  const NAV_JUMP_TARGETS = [
    { label: "Home", action: "internalHome" },
    { label: "Buildings", action: "internalBuildings" },
    { label: "Economy", action: "internalEconomy" },
    { label: "Research", action: "internalResearch" },
    { label: "Galaxy", action: "internalGalaxy" }
  ];
  const OperationsView = () => {
    var _a;
    const {
      dashboard,
      activePage,
      setActivePage,
      nowEpochSec,
      showFeedback,
      apiCommand: apiCommand2,
      loadDashboard,
      uiPrefs,
      setUiPref,
      enqueueManualCommand
    } = useConsole();
    const queueFilter = uiPrefs.queueFilter || "all";
    const allActions = reactExports.useMemo(() => (dashboard == null ? void 0 : dashboard.plan) || [], [dashboard == null ? void 0 : dashboard.plan]);
    const manualList = reactExports.useMemo(() => {
      if (Array.isArray(dashboard == null ? void 0 : dashboard.manual_queue)) {
        return dashboard.manual_queue;
      }
      return allActions.filter(
        (a) => a.manual_queue_id != null && Number(a.manual_queue_id) > 0
      );
    }, [allActions, dashboard]);
    const pinnedCount = manualList.length;
    const filteredActions = reactExports.useMemo(() => {
      if (queueFilter === "ready") {
        return allActions.filter((a) => Boolean(a.executable_now));
      }
      if (queueFilter === "manual") {
        return allActions.filter(
          (a) => a.manual_queue_id != null && Number(a.manual_queue_id) > 0
        );
      }
      return allActions;
    }, [allActions, queueFilter]);
    const cfgMode = String(
      ((_a = dashboard == null ? void 0 : dashboard.config) == null ? void 0 : _a.planner_mode) || (dashboard == null ? void 0 : dashboard.planner_mode) || "priority"
    ).toLowerCase();
    const isGaNn = cfgMode === "ga_nn";
    const modeLabel = isGaNn ? "MODE: GA+NN" : "MODE: PRIORITY";
    const handleDispatch = reactExports.useCallback(
      async (action, dryRun) => {
        const payload = actionPayloadFor(action, dryRun);
        if (!payload) return;
        try {
          showFeedback(
            `${dryRun ? "Simulating" : "Executing"} ${action.target_name || action.kind}...`,
            "ok"
          );
          const res = await apiCommand2(payload);
          showFeedback(res.message || `Command ${payload.command} completed.`, "ok");
          await loadDashboard({ refresh: false, force: true });
        } catch (err) {
          showFeedback(`Action failed: ${err.message}`, feedbackKindForError(err));
        }
      },
      [apiCommand2, loadDashboard, showFeedback]
    );
    const handleUpdateQueue = reactExports.useCallback(
      async (queueId, patch) => {
        try {
          const res = await apiCommand2({ command: "queue_update", queue_id: queueId, ...patch });
          showFeedback(res.message || `Updated queue item #${queueId}.`, "ok");
          await loadDashboard({ refresh: false, force: true });
        } catch (err) {
          showFeedback(`Queue update failed: ${err.message}`, feedbackKindForError(err));
        }
      },
      [apiCommand2, loadDashboard, showFeedback]
    );
    const handleCancelQueue = reactExports.useCallback(
      async (queueId) => {
        try {
          const res = await apiCommand2({ command: "queue_cancel", queue_id: queueId });
          showFeedback(res.message || `Removed queue item #${queueId}.`, "ok");
          await loadDashboard({ refresh: false, force: true });
        } catch (err) {
          showFeedback(`Cancel failed: ${err.message}`, feedbackKindForError(err));
        }
      },
      [apiCommand2, loadDashboard, showFeedback]
    );
    const handleClearQueue = reactExports.useCallback(async () => {
      try {
        const res = await apiCommand2({ command: "queue_clear" });
        showFeedback(res.message || "Cleared manual queue.", "ok");
        await loadDashboard({ refresh: false, force: true });
      } catch (err) {
        showFeedback(`Clear queue failed: ${err.message}`, feedbackKindForError(err));
      }
    }, [apiCommand2, loadDashboard, showFeedback]);
    const handlePinQueue = reactExports.useCallback(
      (act, priority = "P1") => {
        void enqueueManualCommand(buildPinPayloadFromAction(act, priority));
      },
      [enqueueManualCommand]
    );
    const handleNavJump = reactExports.useCallback(
      async (action) => {
        try {
          await apiCommand2({ command: "navigate", action });
          showFeedback(`Chrome switched to ${action}.`, "ok");
        } catch (err) {
          showFeedback(`Navigation failed: ${err.message}`, feedbackKindForError(err));
        }
      },
      [apiCommand2, showFeedback]
    );
    return /* @__PURE__ */ jsxRuntimeExports.jsx(
      "section",
      {
        id: "view-operations",
        className: "page-view",
        "aria-label": "Operations priority queue",
        hidden: activePage !== "operations",
        children: /* @__PURE__ */ jsxRuntimeExports.jsxs("article", { className: "hud-window page-fill", children: [
          /* @__PURE__ */ jsxRuntimeExports.jsxs("div", { className: "window-bar window-bar-wrap", children: [
            /* @__PURE__ */ jsxRuntimeExports.jsxs("div", { className: "window-title-group", children: [
              /* @__PURE__ */ jsxRuntimeExports.jsx("span", { className: "module-code tabular", children: "OPS" }),
              /* @__PURE__ */ jsxRuntimeExports.jsx("h2", { children: "Priority Action Queue (P0 → P3)" }),
              /* @__PURE__ */ jsxRuntimeExports.jsx("span", { className: "module-meta tabular", id: "plan-count-badge", children: `${filteredActions.length} TASKS` }),
              /* @__PURE__ */ jsxRuntimeExports.jsx("span", { className: "module-meta tabular", id: "ops-pinned-badge", hidden: pinnedCount === 0, children: `${pinnedCount} PINNED` }),
              /* @__PURE__ */ jsxRuntimeExports.jsx(
                "span",
                {
                  className: `module-meta tabular mode-badge${isGaNn ? " is-ga" : ""}`,
                  id: "ops-planner-mode-badge",
                  children: modeLabel
                }
              )
            ] }),
            /* @__PURE__ */ jsxRuntimeExports.jsxs("div", { className: "filter-toolbar", children: [
              /* @__PURE__ */ jsxRuntimeExports.jsxs("div", { className: "filter-chips", role: "group", "aria-label": "Queue filters", children: [
                /* @__PURE__ */ jsxRuntimeExports.jsx(
                  "button",
                  {
                    type: "button",
                    className: `chip-btn${queueFilter === "all" ? " active" : ""}`,
                    "data-filter": "all",
                    onClick: () => setUiPref("queueFilter", "all"),
                    children: "All Planned"
                  }
                ),
                /* @__PURE__ */ jsxRuntimeExports.jsx(
                  "button",
                  {
                    type: "button",
                    className: `chip-btn${queueFilter === "ready" ? " active" : ""}`,
                    "data-filter": "ready",
                    onClick: () => setUiPref("queueFilter", "ready"),
                    children: "Executable Only"
                  }
                ),
                /* @__PURE__ */ jsxRuntimeExports.jsx(
                  "button",
                  {
                    type: "button",
                    className: `chip-btn${queueFilter === "manual" ? " active" : ""}`,
                    "data-filter": "manual",
                    id: "chip-filter-manual",
                    onClick: () => setUiPref("queueFilter", "manual"),
                    children: `Pinned Only (${pinnedCount})`
                  }
                )
              ] }),
              /* @__PURE__ */ jsxRuntimeExports.jsxs("div", { className: "inline-actions nav-matrix-inline", children: [
                /* @__PURE__ */ jsxRuntimeExports.jsx(
                  "button",
                  {
                    type: "button",
                    className: "btn btn-secondary btn-sm btn-danger-ghost",
                    id: "btn-mq-clear",
                    hidden: pinnedCount === 0,
                    onClick: handleClearQueue,
                    children: pinnedCount > 0 ? `Clear Pinned (${pinnedCount})` : "Clear Pinned"
                  }
                ),
                /* @__PURE__ */ jsxRuntimeExports.jsx(
                  "button",
                  {
                    type: "button",
                    className: "btn btn-secondary btn-sm",
                    "data-goto-page": "action-graph",
                    onClick: () => setActivePage("action-graph"),
                    children: "Action Graph →"
                  }
                ),
                NAV_JUMP_TARGETS.map((item) => /* @__PURE__ */ jsxRuntimeExports.jsx(
                  "button",
                  {
                    type: "button",
                    className: "btn btn-secondary btn-sm nav-jump",
                    "data-action": item.action,
                    onClick: () => handleNavJump(item.action),
                    children: item.label
                  },
                  item.action
                ))
              ] })
            ] })
          ] }),
          /* @__PURE__ */ jsxRuntimeExports.jsx(ManualQueueComposer, {}),
          /* @__PURE__ */ jsxRuntimeExports.jsx("div", { className: "table-wrap table-scroll", children: /* @__PURE__ */ jsxRuntimeExports.jsxs("table", { id: "ops-plan-table", children: [
            /* @__PURE__ */ jsxRuntimeExports.jsx("thead", { children: /* @__PURE__ */ jsxRuntimeExports.jsxs("tr", { children: [
              /* @__PURE__ */ jsxRuntimeExports.jsx("th", { children: "Priority" }),
              /* @__PURE__ */ jsxRuntimeExports.jsx("th", { children: "Action" }),
              /* @__PURE__ */ jsxRuntimeExports.jsx("th", { children: "Cost / Missing" }),
              /* @__PURE__ */ jsxRuntimeExports.jsx("th", { className: "col-right", children: "Wait ETA" }),
              /* @__PURE__ */ jsxRuntimeExports.jsx("th", { className: "col-right", children: "Total ETA" }),
              /* @__PURE__ */ jsxRuntimeExports.jsx("th", { children: "Reason" }),
              /* @__PURE__ */ jsxRuntimeExports.jsx("th", { className: "col-right", children: "Execute" })
            ] }) }),
            /* @__PURE__ */ jsxRuntimeExports.jsx("tbody", { id: "plan-tbody", children: filteredActions.length === 0 ? /* @__PURE__ */ jsxRuntimeExports.jsx("tr", { children: /* @__PURE__ */ jsxRuntimeExports.jsx("td", { colSpan: 7, className: "empty-cell", children: dashboard ? "No matching actions in queue." : "Waiting for plan..." }) }) : filteredActions.map((act, idx) => /* @__PURE__ */ jsxRuntimeExports.jsx(
              PlanActionRow,
              {
                action: act,
                compact: false,
                nowEpochSec,
                onDispatch: handleDispatch,
                onUpdateQueue: handleUpdateQueue,
                onCancelQueue: handleCancelQueue,
                onPinQueue: handlePinQueue
              },
              `${act.manual_queue_id ?? "auto"}-${act.kind}-${act.planet_id}-${act.target_id ?? act.target_name}-${idx}`
            )) })
          ] }) })
        ] })
      }
    );
  };
  function useDragScroll(scrollRef, dragThresholdPx = 4) {
    const [isDragging, setIsDragging] = reactExports.useState(false);
    const activeRef = reactExports.useRef(false);
    const wasDraggedRef = reactExports.useRef(false);
    const suppressNextClickRef = reactExports.useRef(false);
    const startXRef = reactExports.useRef(0);
    const startYRef = reactExports.useRef(0);
    const startScrollLeftRef = reactExports.useRef(0);
    const startScrollTopRef = reactExports.useRef(0);
    const endDrag = reactExports.useCallback(() => {
      if (!activeRef.current && !isDragging) return;
      const didDrag = wasDraggedRef.current;
      activeRef.current = false;
      wasDraggedRef.current = false;
      setIsDragging(false);
      if (typeof globalThis !== "undefined" && globalThis.state) {
        globalThis.state.agSuppressNodeClick = didDrag;
      }
      if (didDrag) {
        suppressNextClickRef.current = true;
        setTimeout(() => {
          suppressNextClickRef.current = false;
          if (typeof globalThis !== "undefined" && globalThis.state) {
            globalThis.state.agSuppressNodeClick = false;
          }
        }, 0);
      } else {
        suppressNextClickRef.current = false;
      }
    }, [isDragging]);
    const onMouseDown = reactExports.useCallback(
      (e) => {
        if (e.button !== void 0 && e.button !== 0) return;
        const el = scrollRef.current;
        if (!el) return;
        activeRef.current = true;
        wasDraggedRef.current = false;
        suppressNextClickRef.current = false;
        if (typeof globalThis !== "undefined" && globalThis.state) {
          globalThis.state.agSuppressNodeClick = false;
        }
        startXRef.current = safeFiniteNumber(e.clientX, 0) ?? 0;
        startYRef.current = safeFiniteNumber(e.clientY, 0) ?? 0;
        startScrollLeftRef.current = safeFiniteNumber(el.scrollLeft, 0) ?? 0;
        startScrollTopRef.current = safeFiniteNumber(el.scrollTop, 0) ?? 0;
      },
      [scrollRef]
    );
    const applyMove = reactExports.useCallback(
      (clientX, clientY) => {
        if (!activeRef.current) return;
        const el = scrollRef.current;
        if (!el) return;
        const dx = clientX - startXRef.current;
        const dy = clientY - startYRef.current;
        if (!wasDraggedRef.current) {
          if (Math.hypot(dx, dy) <= dragThresholdPx) {
            return;
          }
          wasDraggedRef.current = true;
          setIsDragging(true);
        }
        el.scrollLeft = Math.max(0, startScrollLeftRef.current - dx);
        el.scrollTop = Math.max(0, startScrollTopRef.current - dy);
      },
      [dragThresholdPx, scrollRef]
    );
    const onMouseMove = reactExports.useCallback(
      (e) => {
        if (!activeRef.current) return;
        const cx = safeFiniteNumber(e.clientX, 0) ?? 0;
        const cy = safeFiniteNumber(e.clientY, 0) ?? 0;
        applyMove(cx, cy);
      },
      [applyMove]
    );
    reactExports.useEffect(() => {
      if (typeof window === "undefined") return;
      const handleWinUp = () => endDrag();
      const handleWinBlur = () => endDrag();
      window.addEventListener("mouseup", handleWinUp);
      window.addEventListener("blur", handleWinBlur);
      return () => {
        window.removeEventListener("mouseup", handleWinUp);
        window.removeEventListener("blur", handleWinBlur);
      };
    }, [endDrag]);
    const consumeSuppressClick = reactExports.useCallback(() => {
      if (suppressNextClickRef.current) {
        suppressNextClickRef.current = false;
        if (typeof globalThis !== "undefined" && globalThis.state) {
          globalThis.state.agSuppressNodeClick = false;
        }
        return true;
      }
      return false;
    }, []);
    return {
      isDragging,
      wasDraggedRef,
      consumeSuppressClick,
      onMouseDown,
      onMouseMove,
      onMouseUp: endDrag,
      onMouseLeave: endDrag
    };
  }
  const useClientSyncEffect$1 = typeof window !== "undefined" ? reactExports.useLayoutEffect : reactExports.useEffect;
  const AG_BOOL_FIELDS = [
    "auto_throttle_blast_furnace",
    "merchant_mode",
    "allow_research",
    "allow_shipyard",
    "allow_multi_planet",
    "allow_fleetsave",
    "allow_galaxy_scan",
    "allow_raids",
    "allow_pvp_in_az",
    "allow_colonize",
    "allow_transmitter",
    "forbid_planetary_solar"
  ];
  const AG_NUM_DEFAULTS = {
    ga_population_size: 24,
    ga_generations: 12,
    ga_horizon_steps: 6,
    ga_mutation_rate: 0.25,
    ga_nn_weight: 0.35,
    storage_warn_ratio: 0.85,
    blast_pig_iron_max_share: 0.7,
    combat_safety_margin: 1.4,
    raid_min_loot_spice_eq: 1500,
    hot_planet_solar_temp_threshold: 40,
    min_free_fields_warning: 20,
    min_colony_fields: 300,
    max_planets: 11,
    galaxy_sweep_interval_seconds: 270,
    galaxy_sweep_pages: 100,
    galaxy_scan_click_delay_seconds: 1.5,
    galaxy_scan_click_jitter_seconds: 0.6,
    reserved_spy_slots: 1,
    fleet_save_fuel_reserve_spice: 500,
    action_batch_interval_seconds: 120,
    max_actions_per_hour: 60
  };
  const AG_SEMANTIC_EDGE_TYPES = /* @__PURE__ */ new Set([
    "priority_gate",
    "prerequisite",
    "level_chain",
    "energy_support",
    "throttle_control",
    "storage_cap",
    "resource_feed",
    "loot_feed"
  ]);
  function sanitizeMainWindowText(rawText) {
    return String(rawText ?? "").replace(/generations?/gi, "cycles");
  }
  function edgeVisualSpec(edge) {
    const pType = String((edge == null ? void 0 : edge.primary_type) || "execution_order");
    if (pType === "priority_gate") {
      return { cls: "is-gate", marker: "ag-arrow-gate" };
    }
    if (pType === "prerequisite" || pType === "level_chain") {
      return { cls: "is-prereq", marker: "ag-arrow-prereq" };
    }
    if (pType === "energy_support" || pType === "throttle_control" || pType === "storage_cap" || pType === "resource_feed" || pType === "loot_feed") {
      return { cls: "is-resource", marker: "ag-arrow-resource" };
    }
    if (pType === "queue_order") {
      return { cls: "is-queue", marker: "ag-arrow-queue" };
    }
    return { cls: "is-seq", marker: "ag-arrow-seq" };
  }
  function edgeMatchesFilter(edge, filterMode) {
    if (filterMode !== "deps") return true;
    const types = Array.isArray(edge == null ? void 0 : edge.types) ? edge.types : [edge == null ? void 0 : edge.primary_type];
    return types.some((t) => AG_SEMANTIC_EDGE_TYPES.has(String(t || "")));
  }
  function formatNodeTargetLabel(node) {
    const base = String((node == null ? void 0 : node.target_name) || (node == null ? void 0 : node.kind) || "Action");
    const lvl = safeFiniteNumber(node == null ? void 0 : node.target_level, null);
    const kind = String((node == null ? void 0 : node.kind) || "");
    if (lvl === null) return base;
    if (kind === "THROTTLE_BUILDING") {
      return `${base} → ${Math.round(lvl)}%`;
    }
    if (kind === "BUILD_SHIP") {
      if (/\(x\d+\)$/i.test(base)) return base;
      return `${base} (x${Math.max(1, Math.round(lvl))})`;
    }
    if (kind === "BUILD" || kind === "WAIT_RESOURCES" || kind === "RESEARCH") {
      return `${base} L${Math.round(lvl)}`;
    }
    if (kind === "DEMOLISH_BUILDING") {
      return `Demolish ${base} → L${Math.round(lvl)}`;
    }
    return base;
  }
  function formatNodeStatusShort(status) {
    if (status === "ready") return "READY";
    if (status === "waiting_resources") return "WAIT";
    if (status === "blocked_gate") return "HOLD";
    return "QUEUE";
  }
  function buildDraftStateFromConfig(cfg) {
    const out = {
      planner_mode: String((cfg == null ? void 0 : cfg.planner_mode) || "priority").toLowerCase() === "ga_nn" ? "ga_nn" : "priority",
      playstyle: String((cfg == null ? void 0 : cfg.playstyle) || "fleeter"),
      ga_seed: (cfg == null ? void 0 : cfg.ga_seed) != null ? String(cfg.ga_seed) : ""
    };
    for (const [k, fallback] of Object.entries(AG_NUM_DEFAULTS)) {
      const v = safeFiniteNumber(cfg == null ? void 0 : cfg[k], fallback) ?? fallback;
      out[k] = String(v);
    }
    for (const bKey of AG_BOOL_FIELDS) {
      const defChecked = bKey === "auto_throttle_blast_furnace" || bKey === "allow_research" || bKey === "allow_shipyard" || bKey === "allow_multi_planet";
      out[bKey] = cfg && bKey in cfg ? Boolean(cfg[bKey]) : defChecked;
    }
    return out;
  }
  const ActionGraphView = () => {
    var _a;
    const {
      dashboard,
      setDashboard,
      activePage,
      apiOnline,
      actionGraphData,
      setActionGraphData,
      agSimDirty,
      setAgSimDirty,
      setAgSimRunning,
      agHydrationTick,
      setConfigDirty,
      setConfigSaving,
      bumpDashboardSeq,
      beginGaCalc,
      endGaCalc,
      uiPrefs,
      setUiPref,
      apiCommand: apiCommand2,
      loadDashboard,
      showFeedback
    } = useConsole();
    const scrollRef = reactExports.useRef(null);
    const debounceRef = reactExports.useRef(null);
    const {
      isDragging,
      consumeSuppressClick,
      onMouseDown,
      onMouseMove,
      onMouseUp,
      onMouseLeave
    } = useDragScroll(scrollRef, 4);
    const [selectedNodeId, setSelectedNodeId] = reactExports.useState(null);
    const [draftValues, setDraftValues] = reactExports.useState(
      () => buildDraftStateFromConfig(dashboard == null ? void 0 : dashboard.config)
    );
    const populateControls = reactExports.useCallback((cfg) => {
      if (!cfg || typeof cfg !== "object") return;
      const next = buildDraftStateFromConfig(cfg);
      setDraftValues(next);
      if (typeof document !== "undefined") {
        for (const [k, v] of Object.entries(next)) {
          const node = document.getElementById(`ag-cfg-${k}`);
          if (!node) continue;
          if (node instanceof HTMLInputElement && node.type === "checkbox") {
            node.checked = Boolean(v);
          } else {
            node.value = String(v ?? "");
          }
        }
      }
    }, []);
    reactExports.useEffect(() => {
      const gState = globalThis.state;
      if (!(gState == null ? void 0 : gState.agSimDirty) && !((gState == null ? void 0 : gState.agSimRunning) > 0) && !((gState == null ? void 0 : gState.configSaving) > 0) && (dashboard == null ? void 0 : dashboard.config)) {
        populateControls(dashboard.config);
      }
    }, [agHydrationTick, dashboard == null ? void 0 : dashboard.config, populateControls]);
    const syncDraftValuesFromDom = reactExports.useCallback(
      (modeOverride) => {
        const readEl = (id) => typeof document !== "undefined" ? document.getElementById(id) : null;
        setDraftValues((prev) => {
          const modeSel = readEl("ag-cfg-planner_mode");
          const playSel = readEl("ag-cfg-playstyle");
          const seedInp = readEl("ag-cfg-ga_seed");
          const next = {
            ...prev,
            planner_mode: modeOverride ?? (modeSel ? String(modeSel.value || "priority") : String(prev.planner_mode || "priority")),
            playstyle: playSel ? String(playSel.value || "fleeter") : String(prev.playstyle || "fleeter"),
            ga_seed: seedInp ? String(seedInp.value ?? "") : String(prev.ga_seed ?? "")
          };
          for (const boolField of AG_BOOL_FIELDS) {
            const box = readEl(`ag-cfg-${boolField}`);
            if (box) next[boolField] = Boolean(box.checked);
          }
          for (const numField of Object.keys(AG_NUM_DEFAULTS)) {
            const inp = readEl(`ag-cfg-${numField}`);
            if (inp) next[numField] = String(inp.value ?? "");
          }
          return next;
        });
      },
      []
    );
    const collectDraftPayload = reactExports.useCallback(
      (modeOverride) => {
        const readEl = (id) => typeof document !== "undefined" ? document.getElementById(id) : null;
        const modeSel = readEl("ag-cfg-planner_mode");
        const playSel = readEl("ag-cfg-playstyle");
        const payload = {
          planner_mode: modeOverride ?? (modeSel ? String(modeSel.value || "priority") : String(draftValues.planner_mode || "priority")),
          playstyle: playSel ? String(playSel.value || "fleeter") : String(draftValues.playstyle || "fleeter")
        };
        for (const boolField of AG_BOOL_FIELDS) {
          const box = readEl(`ag-cfg-${boolField}`);
          payload[boolField] = box ? Boolean(box.checked) : Boolean(draftValues[boolField]);
        }
        for (const numField of Object.keys(AG_NUM_DEFAULTS)) {
          const inp = readEl(`ag-cfg-${numField}`);
          const raw = inp ? String(inp.value ?? "").trim() : String(draftValues[numField] ?? "").trim();
          if (raw === "") {
            payload[numField] = "";
          } else if (/^[+-]?(\d+\.?\d*|\.\d+)([eE][+-]?\d+)?$/.test(raw)) {
            const parsed = Number(raw);
            payload[numField] = Number.isFinite(parsed) ? parsed : raw;
          } else {
            payload[numField] = raw;
          }
        }
        const seedInp = readEl("ag-cfg-ga_seed");
        const rawSeed = seedInp ? String(seedInp.value ?? "").trim() : String(draftValues.ga_seed ?? "").trim();
        if (rawSeed === "") {
          payload.ga_seed = null;
        } else if (/^-?\d+$/.test(rawSeed)) {
          const parsedSeed = Number(rawSeed);
          payload.ga_seed = Number.isSafeInteger(parsedSeed) ? parsedSeed : rawSeed;
        } else {
          payload.ga_seed = rawSeed;
        }
        return payload;
      },
      [draftValues]
    );
    const refreshActiveActionGraph = reactExports.useCallback(
      async (forceRefresh = false) => {
        const gState = globalThis.state;
        const reqSeq = gState ? ++gState.agSimSeq : 1;
        try {
          const res = await apiCommand2({
            command: "action_graph",
            simulated: false,
            refresh: Boolean(forceRefresh)
          });
          if (gState && (reqSeq !== gState.agSimSeq || gState.agSimDirty)) return false;
          if (res.action_graph && typeof res.action_graph === "object") {
            setActionGraphData(res.action_graph);
            const curDash = (gState == null ? void 0 : gState.dashboard) || dashboard;
            if (curDash) {
              const nextDash = { ...curDash, action_graph: res.action_graph };
              if (gState) gState.dashboard = nextDash;
              setDashboard(nextDash);
            }
            return true;
          }
        } catch (err) {
          if (!gState || reqSeq === gState.agSimSeq) {
            showFeedback(`Action graph load failed: ${err.message}`, feedbackKindForError(err));
          }
        }
        return false;
      },
      [apiCommand2, dashboard, setActionGraphData, setDashboard, showFeedback]
    );
    const runAgSimulation = reactExports.useCallback(
      async ({
        explicit = false,
        modeOverride
      } = {}) => {
        if (debounceRef.current) {
          clearTimeout(debounceRef.current);
          debounceRef.current = null;
        }
        const draft = collectDraftPayload(modeOverride);
        const simMode = String(draft.planner_mode || "priority").toLowerCase() === "ga_nn" ? "ga_nn" : "priority";
        syncDraftValuesFromDom(simMode);
        setAgSimDirty(true);
        const gState = globalThis.state;
        const reqSeq = gState ? ++gState.agSimSeq : 1;
        if (gState) gState.agSimRunning = (gState.agSimRunning || 0) + 1;
        setAgSimRunning((c) => c + 1);
        const useGaHud = simMode === "ga_nn";
        if (useGaHud) beginGaCalc();
        try {
          const res = await apiCommand2({
            command: "action_graph",
            planner_mode: simMode,
            config: draft,
            simulated: true,
            refresh: false
          });
          if (gState && reqSeq !== gState.agSimSeq) return false;
          if (res.action_graph && typeof res.action_graph === "object") {
            const isDirty = Boolean(res.action_graph.is_dirty);
            setActionGraphData(res.action_graph);
            const curDash = (gState == null ? void 0 : gState.dashboard) || dashboard;
            if (!isDirty && curDash) {
              const nextDash = { ...curDash, action_graph: res.action_graph };
              if (gState) gState.dashboard = nextDash;
              setDashboard(nextDash);
            }
            setAgSimDirty(isDirty);
            if (explicit) {
              const cnt = Array.isArray(res.action_graph.nodes) ? res.action_graph.nodes.length : 0;
              showFeedback(
                `Simulated ${simMode} action graph (${cnt} planned ${cnt === 1 ? "action" : "actions"} — active config unchanged).`,
                "ok"
              );
            }
            return true;
          }
          return false;
        } catch (err) {
          if (!gState || reqSeq === gState.agSimSeq) {
            showFeedback(`Simulation preview error: ${err.message}`, feedbackKindForError(err));
          }
          return false;
        } finally {
          if (gState) gState.agSimRunning = Math.max(0, (gState.agSimRunning || 0) - 1);
          setAgSimRunning((c) => Math.max(0, c - 1));
          if (useGaHud) endGaCalc();
        }
      },
      [
        apiCommand2,
        beginGaCalc,
        collectDraftPayload,
        dashboard,
        endGaCalc,
        setActionGraphData,
        setAgSimDirty,
        setAgSimRunning,
        setDashboard,
        showFeedback,
        syncDraftValuesFromDom
      ]
    );
    useClientSyncEffect$1(() => {
      globalThis.runAgSimulation = runAgSimulation;
      globalThis.populateAgParamControls = populateControls;
    }, [populateControls, runAgSimulation]);
    const scheduleDebouncedSim = reactExports.useCallback(() => {
      const gState = globalThis.state;
      if (gState) gState.agSimSeq = (gState.agSimSeq || 0) + 1;
      setAgSimDirty(true);
      if (debounceRef.current) {
        clearTimeout(debounceRef.current);
      }
      debounceRef.current = setTimeout(() => {
        debounceRef.current = null;
        runAgSimulation({ explicit: false });
      }, 180);
    }, [runAgSimulation, setAgSimDirty]);
    const onModeSwitchClick = (targetMode) => {
      const modeSel = typeof document !== "undefined" ? document.getElementById("ag-cfg-planner_mode") : null;
      if (modeSel) {
        modeSel.value = targetMode;
      }
      syncDraftValuesFromDom(targetMode);
      void runAgSimulation({ explicit: false, modeOverride: targetMode });
    };
    const onResetSim = async () => {
      var _a2;
      if (debounceRef.current) {
        clearTimeout(debounceRef.current);
        debounceRef.current = null;
      }
      const gState = globalThis.state;
      if (gState) gState.agSimSeq = (gState.agSimSeq || 0) + 1;
      setAgSimDirty(false);
      const activeCfg = ((_a2 = (gState == null ? void 0 : gState.dashboard) || dashboard) == null ? void 0 : _a2.config) || null;
      if (activeCfg) {
        populateControls(activeCfg);
      }
      const curDash = (gState == null ? void 0 : gState.dashboard) || dashboard;
      if (curDash == null ? void 0 : curDash.action_graph) {
        setActionGraphData(curDash.action_graph);
      } else if (curDash) {
        setActionGraphData(buildClientFallbackActionGraph(curDash));
      }
      if (apiOnline) {
        await refreshActiveActionGraph(false);
      }
      showFeedback("Reset simulation controls to active StrategyConfig.", "ok");
    };
    const onApplySim = async () => {
      var _a2;
      if (debounceRef.current) {
        clearTimeout(debounceRef.current);
        debounceRef.current = null;
      }
      syncDraftValuesFromDom();
      const payload = collectDraftPayload();
      const isGa = String(payload.planner_mode || "").toLowerCase() === "ga_nn";
      if (isGa) beginGaCalc();
      const gState = globalThis.state;
      if (gState) {
        gState.agSimSeq = (gState.agSimSeq || 0) + 1;
        gState.configSaving = (gState.configSaving || 0) + 1;
      }
      bumpDashboardSeq();
      setConfigSaving((c) => c + 1);
      try {
        const res = await apiCommand2({
          command: "update_config",
          config: payload,
          include_action_graph: true
        });
        bumpDashboardSeq();
        setConfigDirty(false);
        setAgSimDirty(false);
        if (res.config && typeof res.config === "object") {
          populateControls(res.config);
          (_a2 = globalThis.renderConfigForm) == null ? void 0 : _a2.call(globalThis, res.config);
          const curDash = (gState == null ? void 0 : gState.dashboard) || dashboard;
          const nextDash = curDash ? {
            ...curDash,
            config: res.config,
            planner_mode: res.config.planner_mode || curDash.planner_mode,
            action_graph: res.action_graph || curDash.action_graph
          } : {
            config: res.config,
            planner_mode: res.config.planner_mode || "priority",
            action_graph: res.action_graph || null
          };
          if (gState) gState.dashboard = nextDash;
          setDashboard(nextDash);
        }
        if (res.action_graph && typeof res.action_graph === "object") {
          setActionGraphData(res.action_graph);
        }
        showFeedback("Applied parameters to active StrategyConfig (config.yaml saved).", "ok");
        await loadDashboard({ refresh: false, preserveConfig: false, force: true });
      } catch (err) {
        showFeedback(`Failed applying parameters: ${err.message}`, feedbackKindForError(err));
      } finally {
        if (gState) gState.configSaving = Math.max(0, (gState.configSaving || 0) - 1);
        setConfigSaving((c) => Math.max(0, c - 1));
        if (isGa) endGaCalc();
      }
    };
    const graph = actionGraphData || (dashboard ? buildClientFallbackActionGraph(dashboard) : null);
    const nodes = Array.isArray(graph == null ? void 0 : graph.nodes) ? graph.nodes : [];
    const allEdges = Array.isArray(graph == null ? void 0 : graph.edges) ? graph.edges : [];
    const edgeFilter = uiPrefs.agEdgeFilter || "all";
    const paramTab = uiPrefs.agParamTab || "core";
    const visibleEdges = allEdges.filter((e) => edgeMatchesFilter(e, edgeFilter));
    const summary = (graph == null ? void 0 : graph.summary) || {};
    const graphMode = String((graph == null ? void 0 : graph.mode) || draftValues.planner_mode || "priority").toLowerCase() === "ga_nn" ? "ga_nn" : "priority";
    const activeSelectedId = selectedNodeId && nodes.some((n) => n.id === selectedNodeId) ? selectedNodeId : null;
    const totalNodes = Math.max(
      0,
      Math.round(safeFiniteNumber(summary.total_nodes, nodes.length) ?? nodes.length)
    );
    const readyCount = Math.max(
      0,
      Math.round(
        safeFiniteNumber(
          summary.ready_count,
          nodes.filter((n) => n.status === "ready").length
        ) ?? 0
      )
    );
    const waitingCount = Math.max(
      0,
      Math.round(
        safeFiniteNumber(
          summary.waiting_count,
          nodes.filter((n) => n.status === "waiting_resources").length
        ) ?? 0
      )
    );
    const blockedCount = Math.max(
      0,
      Math.round(
        safeFiniteNumber(
          summary.blocked_count,
          nodes.filter((n) => n.status === "blocked_gate").length
        ) ?? 0
      )
    );
    const byEdgeType = summary.by_edge_type || {};
    const prereqCount = Math.round(safeFiniteNumber(byEdgeType.prerequisite, 0) ?? 0) + Math.round(safeFiniteNumber(byEdgeType.level_chain, 0) ?? 0) + Math.round(safeFiniteNumber(byEdgeType.priority_gate, 0) ?? 0);
    const resCount = Math.round(safeFiniteNumber(byEdgeType.energy_support, 0) ?? 0) + Math.round(safeFiniteNumber(byEdgeType.throttle_control, 0) ?? 0) + Math.round(safeFiniteNumber(byEdgeType.storage_cap, 0) ?? 0) + Math.round(safeFiniteNumber(byEdgeType.resource_feed, 0) ?? 0) + Math.round(safeFiniteNumber(byEdgeType.loot_feed, 0) ?? 0);
    const gaSum = graph == null ? void 0 : graph.ga_summary;
    const engineTitle = graphMode === "ga_nn" && gaSum && typeof gaSum === "object" ? sanitizeMainWindowText(
      `GA+NN · ${fmtImprovementPct(
        gaSum.improvement_pct,
        safeFiniteNumber(gaSum.baseline_fitness ?? gaSum.initial_best_fitness, null),
        safeFiniteNumber(gaSum.best_fitness ?? gaSum.final_best_fitness, null)
      )}`
    ) : graphMode === "ga_nn" ? "GA+NN · EVOLUTIONARY" : "PRIORITY · P0–P3";
    const engineSub = graphMode === "ga_nn" && gaSum && typeof gaSum === "object" ? `Pop ${Math.max(1, Math.round(safeFiniteNumber(gaSum.population_size, 24) ?? 24))} · ${Math.max(1, Math.round(safeFiniteNumber(gaSum.generations, 12) ?? 12))} cycles · ${Math.max(0, Math.round(safeFiniteNumber(gaSum.elapsed_ms, 0) ?? 0))}ms` : sanitizeMainWindowText(
      `${Boolean((graph == null ? void 0 : graph.is_dirty) ?? (graph == null ? void 0 : graph.simulated)) ? "Simulated" : "Active"} · ${String(
        ((_a = graph == null ? void 0 : graph.effective_config) == null ? void 0 : _a.playstyle) || "fleeter"
      )}`
    );
    const LANE_HEADER_W = 168;
    const PAD_X = 20;
    const PAD_Y = 14;
    const CARD_W = 228;
    const CARD_H = 92;
    const COL_GAP = 68;
    const STACK_GAP = 14;
    const LANE_PAD_Y = 16;
    const rawLanes = Array.isArray(graph == null ? void 0 : graph.lanes) ? graph.lanes : [];
    const activeLanes = [];
    const laneIdxById = /* @__PURE__ */ new Map();
    rawLanes.forEach((ln) => {
      const lid = String((ln == null ? void 0 : ln.id) || "");
      if (!lid) return;
      if (nodes.some((n) => n.lane_id === lid)) {
        laneIdxById.set(lid, activeLanes.length);
        activeLanes.push(ln);
      }
    });
    nodes.forEach((n) => {
      const lid = String(n.lane_id || "lane:global");
      if (!laneIdxById.has(lid)) {
        laneIdxById.set(lid, activeLanes.length);
        activeLanes.push({
          id: lid,
          scope: lid === "lane:global" ? "global" : "planet",
          label: lid === "lane:global" ? "Empire & Global Research" : n.planet_coords ? `${n.planet_name || "Colony"} [${n.planet_coords}]` : String(n.planet_name || lid),
          planet_id: safeFiniteNumber(n.planet_id, 0) ?? 0,
          coords: String(n.planet_coords || ""),
          energy: 0,
          node_count: 1
        });
      }
    });
    const cellStacks = /* @__PURE__ */ new Map();
    const maxStackByLane = new Array(activeLanes.length).fill(1);
    let maxCol = 0;
    const nodePlacement = /* @__PURE__ */ new Map();
    nodes.forEach((n) => {
      const lIdx = laneIdxById.get(String(n.lane_id || "lane:global")) ?? 0;
      const colIdx = Math.max(0, Math.round(safeFiniteNumber(n.topological_layer, 0) ?? 0));
      if (colIdx > maxCol) maxCol = colIdx;
      const cellKey = `${lIdx}:${colIdx}`;
      const stackIdx = cellStacks.get(cellKey) || 0;
      cellStacks.set(cellKey, stackIdx + 1);
      if (stackIdx + 1 > (maxStackByLane[lIdx] || 1)) {
        maxStackByLane[lIdx] = stackIdx + 1;
      }
      nodePlacement.set(n.id, { laneIdx: lIdx, colIdx, stackIdx });
    });
    const laneTops = [];
    const laneHeights = [];
    let cursorY = PAD_Y;
    for (let i = 0; i < activeLanes.length; i += 1) {
      const stacks = maxStackByLane[i] || 1;
      const h = stacks * CARD_H + Math.max(0, stacks - 1) * STACK_GAP + LANE_PAD_Y * 2;
      laneTops.push(cursorY);
      laneHeights.push(h);
      cursorY += h;
    }
    const totalWidth = Math.max(
      680,
      LANE_HEADER_W + PAD_X * 2 + (maxCol + 1) * CARD_W + maxCol * COL_GAP + 24
    );
    const totalHeight = Math.max(240, cursorY + PAD_Y);
    const coordsById = /* @__PURE__ */ new Map();
    nodes.forEach((node) => {
      const plc = nodePlacement.get(node.id) || { laneIdx: 0, colIdx: 0, stackIdx: 0 };
      const x = LANE_HEADER_W + PAD_X + plc.colIdx * (CARD_W + COL_GAP);
      const y = (laneTops[plc.laneIdx] || PAD_Y) + LANE_PAD_Y + plc.stackIdx * (CARD_H + STACK_GAP);
      coordsById.set(node.id, { x, y, colIdx: plc.colIdx, laneIdx: plc.laneIdx });
    });
    const upstreamIds = /* @__PURE__ */ new Set();
    const downstreamIds = /* @__PURE__ */ new Set();
    if (activeSelectedId) {
      allEdges.forEach((e) => {
        if (e.target === activeSelectedId) upstreamIds.add(e.source);
        if (e.source === activeSelectedId) downstreamIds.add(e.target);
      });
    }
    const selectedObj = activeSelectedId ? nodes.find((n) => n.id === activeSelectedId) || null : null;
    const nodesById = new Map(nodes.map((n) => [n.id, n]));
    const incomingSel = selectedObj ? allEdges.filter((e) => e.target === selectedObj.id) : [];
    const outgoingSel = selectedObj ? allEdges.filter((e) => e.source === selectedObj.id) : [];
    const draftModeNorm = String(draftValues.planner_mode || "priority").toLowerCase() === "ga_nn" ? "ga_nn" : "priority";
    const knownPlaystyles = ["fleeter", "hybrid", "miner"];
    const curPlaystyle = String(draftValues.playstyle || "fleeter");
    return /* @__PURE__ */ jsxRuntimeExports.jsxs(
      "section",
      {
        id: "view-action-graph",
        className: "page-view ag-view",
        "aria-label": "Action sequence and dependency graph",
        hidden: activePage !== "action-graph",
        children: [
          /* @__PURE__ */ jsxRuntimeExports.jsxs("article", { className: "hud-window ag-main-window", id: "ag-main-window", children: [
            /* @__PURE__ */ jsxRuntimeExports.jsxs("div", { className: "window-bar window-bar-wrap", children: [
              /* @__PURE__ */ jsxRuntimeExports.jsxs("div", { className: "window-title-group", children: [
                /* @__PURE__ */ jsxRuntimeExports.jsx("span", { className: "module-code tabular", children: "DAG" }),
                /* @__PURE__ */ jsxRuntimeExports.jsx("h2", { children: "Action Sequence & Dependency Graph" }),
                /* @__PURE__ */ jsxRuntimeExports.jsx(
                  "span",
                  {
                    className: `ag-status-pill tabular ${agSimDirty ? "is-sim" : "is-live"}`,
                    id: "ag-sim-status-pill",
                    role: "status",
                    children: agSimDirty ? "SIMULATION PREVIEW · UNSAVED" : "LIVE ACTIVE CONFIG"
                  }
                )
              ] }),
              /* @__PURE__ */ jsxRuntimeExports.jsxs("div", { className: "inline-actions", children: [
                /* @__PURE__ */ jsxRuntimeExports.jsxs(
                  "div",
                  {
                    className: "tab-group",
                    id: "ag-mode-switch",
                    role: "radiogroup",
                    "aria-label": "Action graph planner mode",
                    children: [
                      /* @__PURE__ */ jsxRuntimeExports.jsx(
                        "button",
                        {
                          type: "button",
                          className: `tab-btn${graphMode === "priority" ? " active" : ""}`,
                          id: "ag-mode-priority",
                          role: "radio",
                          "aria-checked": graphMode === "priority" ? "true" : "false",
                          "data-ag-mode": "priority",
                          onClick: () => onModeSwitchClick("priority"),
                          children: "Heuristic (priority)"
                        }
                      ),
                      /* @__PURE__ */ jsxRuntimeExports.jsx(
                        "button",
                        {
                          type: "button",
                          className: `tab-btn${graphMode === "ga_nn" ? " active" : ""}`,
                          id: "ag-mode-ga_nn",
                          role: "radio",
                          "aria-checked": graphMode === "ga_nn" ? "true" : "false",
                          "data-ag-mode": "ga_nn",
                          onClick: () => onModeSwitchClick("ga_nn"),
                          children: "GA + Neural (ga_nn)"
                        }
                      )
                    ]
                  }
                ),
                /* @__PURE__ */ jsxRuntimeExports.jsx(
                  "button",
                  {
                    type: "button",
                    className: "btn btn-secondary btn-sm",
                    id: "btn-ag-refresh",
                    onClick: async () => {
                      if (agSimDirty) {
                        await runAgSimulation({ explicit: true });
                      } else {
                        const ok = await refreshActiveActionGraph(false);
                        if (ok) {
                          showFeedback("Action graph refreshed from empire snapshot.", "ok");
                        }
                      }
                    },
                    children: "Refresh"
                  }
                )
              ] })
            ] }),
            /* @__PURE__ */ jsxRuntimeExports.jsxs("div", { className: "ag-kpi-strip", "aria-label": "Action graph summary metrics", children: [
              /* @__PURE__ */ jsxRuntimeExports.jsxs("div", { className: "ag-kpi-cell", children: [
                /* @__PURE__ */ jsxRuntimeExports.jsx("span", { className: "ag-kpi-label", children: "Planned Actions" }),
                /* @__PURE__ */ jsxRuntimeExports.jsx("strong", { className: "ag-kpi-val tabular", id: "ag-kpi-nodes", children: `${totalNodes} ${totalNodes === 1 ? "node" : "nodes"}` }),
                /* @__PURE__ */ jsxRuntimeExports.jsx("span", { className: "ag-kpi-sub tabular", id: "ag-kpi-ready", children: blockedCount > 0 ? `${readyCount} ready · ${waitingCount} wait · ${blockedCount} hold` : `${readyCount} ready · ${waitingCount} waiting` })
              ] }),
              /* @__PURE__ */ jsxRuntimeExports.jsxs("div", { className: "ag-kpi-cell", children: [
                /* @__PURE__ */ jsxRuntimeExports.jsx("span", { className: "ag-kpi-label", children: "Dependencies & Flow" }),
                /* @__PURE__ */ jsxRuntimeExports.jsx("strong", { className: "ag-kpi-val tabular", id: "ag-kpi-edges", children: `${visibleEdges.length} ${visibleEdges.length === 1 ? "edge" : "edges"}` }),
                /* @__PURE__ */ jsxRuntimeExports.jsx("span", { className: "ag-kpi-sub tabular", id: "ag-kpi-dep-breakdown", children: `${prereqCount} prereq · ${resCount} resource` })
              ] }),
              /* @__PURE__ */ jsxRuntimeExports.jsxs("div", { className: "ag-kpi-cell", children: [
                /* @__PURE__ */ jsxRuntimeExports.jsx("span", { className: "ag-kpi-label", children: "Critical Path ETA" }),
                /* @__PURE__ */ jsxRuntimeExports.jsx("strong", { className: "ag-kpi-val tabular", id: "ag-kpi-horizon", children: sanitizeMainWindowText(summary.critical_path_eta || "Ready") }),
                /* @__PURE__ */ jsxRuntimeExports.jsx("span", { className: "ag-kpi-sub tabular", id: "ag-kpi-bottleneck", children: summary.primary_bottleneck ? `Bottleneck: ${String(summary.primary_bottleneck).replace(/_/g, " ")}` : "No resource bottleneck" })
              ] }),
              /* @__PURE__ */ jsxRuntimeExports.jsxs("div", { className: "ag-kpi-cell", children: [
                /* @__PURE__ */ jsxRuntimeExports.jsx("span", { className: "ag-kpi-label", children: "Engine Telemetry" }),
                /* @__PURE__ */ jsxRuntimeExports.jsx("strong", { className: "ag-kpi-val tabular", id: "ag-kpi-engine", children: engineTitle }),
                /* @__PURE__ */ jsxRuntimeExports.jsx("span", { className: "ag-kpi-sub tabular", id: "ag-kpi-engine-sub", children: engineSub })
              ] })
            ] }),
            /* @__PURE__ */ jsxRuntimeExports.jsxs("div", { className: "ag-legend-bar", children: [
              /* @__PURE__ */ jsxRuntimeExports.jsxs("div", { className: "ag-legend-items", "aria-label": "Graph edge legend", children: [
                /* @__PURE__ */ jsxRuntimeExports.jsxs("span", { className: "ag-legend-item", children: [
                  /* @__PURE__ */ jsxRuntimeExports.jsx("span", { className: "ag-swatch is-seq", "aria-hidden": "true" }),
                  "Sequence Flow"
                ] }),
                /* @__PURE__ */ jsxRuntimeExports.jsxs("span", { className: "ag-legend-item", children: [
                  /* @__PURE__ */ jsxRuntimeExports.jsx("span", { className: "ag-swatch is-prereq", "aria-hidden": "true" }),
                  "Prerequisite Unlock"
                ] }),
                /* @__PURE__ */ jsxRuntimeExports.jsxs("span", { className: "ag-legend-item", children: [
                  /* @__PURE__ */ jsxRuntimeExports.jsx("span", { className: "ag-swatch is-resource", "aria-hidden": "true" }),
                  "Resource / Power / B2%"
                ] }),
                /* @__PURE__ */ jsxRuntimeExports.jsxs("span", { className: "ag-legend-item", children: [
                  /* @__PURE__ */ jsxRuntimeExports.jsx("span", { className: "ag-swatch is-queue", "aria-hidden": "true" }),
                  "Queue / Slot Order"
                ] })
              ] }),
              /* @__PURE__ */ jsxRuntimeExports.jsxs("div", { className: "filter-chips", children: [
                /* @__PURE__ */ jsxRuntimeExports.jsx(
                  "button",
                  {
                    type: "button",
                    className: `chip-btn${edgeFilter === "all" ? " active" : ""}`,
                    "data-ag-edge-filter": "all",
                    onClick: () => setUiPref("agEdgeFilter", "all"),
                    children: "All Links"
                  }
                ),
                /* @__PURE__ */ jsxRuntimeExports.jsx(
                  "button",
                  {
                    type: "button",
                    className: `chip-btn${edgeFilter === "deps" ? " active" : ""}`,
                    "data-ag-edge-filter": "deps",
                    onClick: () => setUiPref("agEdgeFilter", "deps"),
                    children: "Dependencies Only"
                  }
                )
              ] })
            ] }),
            /* @__PURE__ */ jsxRuntimeExports.jsxs(
              "div",
              {
                ref: scrollRef,
                className: `ag-canvas-scroll${isDragging ? " is-dragging" : ""}`,
                id: "ag-canvas-scroll",
                onMouseDown,
                onMouseMove,
                onMouseUp,
                onMouseLeave,
                children: [
                  /* @__PURE__ */ jsxRuntimeExports.jsxs(
                    "div",
                    {
                      className: "ag-canvas-stage",
                      id: "ag-canvas-stage",
                      hidden: nodes.length === 0,
                      style: nodes.length > 0 ? { width: `${totalWidth}px`, height: `${totalHeight}px` } : void 0,
                      children: [
                        /* @__PURE__ */ jsxRuntimeExports.jsx(
                          "div",
                          {
                            className: "ag-lanes-layer",
                            id: "ag-lanes-layer",
                            "aria-hidden": "true",
                            style: nodes.length > 0 ? { width: `${totalWidth}px`, height: `${totalHeight}px` } : void 0,
                            children: activeLanes.map((ln, idx) => {
                              const laneNodeCnt = nodes.filter((n) => n.lane_id === ln.id).length;
                              const eNet = Math.round(safeFiniteNumber(ln.energy, 0) ?? 0);
                              const metaText = ln.scope === "global" ? `${laneNodeCnt} global ${laneNodeCnt === 1 ? "task" : "tasks"}` : `${laneNodeCnt} ${laneNodeCnt === 1 ? "task" : "tasks"} · Energy ${eNet > 0 ? `+${eNet}` : String(eNet)}`;
                              return /* @__PURE__ */ jsxRuntimeExports.jsx(
                                "div",
                                {
                                  className: idx % 2 === 1 ? "ag-lane-band is-alt" : "ag-lane-band",
                                  "data-lane-id": String(ln.id || ""),
                                  style: {
                                    top: `${laneTops[idx]}px`,
                                    width: `${totalWidth}px`,
                                    height: `${laneHeights[idx]}px`
                                  },
                                  children: /* @__PURE__ */ jsxRuntimeExports.jsxs("div", { className: "ag-lane-header", children: [
                                    /* @__PURE__ */ jsxRuntimeExports.jsx("span", { className: "ag-lane-title", children: sanitizeMainWindowText(ln.label || ln.id || "Lane") }),
                                    /* @__PURE__ */ jsxRuntimeExports.jsx("span", { className: "ag-lane-meta tabular", children: metaText })
                                  ] })
                                },
                                String(ln.id || idx)
                              );
                            })
                          }
                        ),
                        /* @__PURE__ */ jsxRuntimeExports.jsx(
                          "div",
                          {
                            className: "ag-edges-layer",
                            id: "ag-edges-layer",
                            "aria-hidden": "true",
                            style: nodes.length > 0 ? { width: `${totalWidth}px`, height: `${totalHeight}px` } : void 0,
                            children: nodes.length > 0 ? /* @__PURE__ */ jsxRuntimeExports.jsxs(
                              "svg",
                              {
                                id: "ag-edges-svg",
                                viewBox: `0 0 ${totalWidth} ${totalHeight}`,
                                width: totalWidth,
                                height: totalHeight,
                                children: [
                                  /* @__PURE__ */ jsxRuntimeExports.jsx("defs", { children: [
                                    ["ag-arrow-seq", "#32e8ff"],
                                    ["ag-arrow-prereq", "#16ec98"],
                                    ["ag-arrow-resource", "#efb03b"],
                                    ["ag-arrow-queue", "#9eb3b8"],
                                    ["ag-arrow-gate", "#ff6873"]
                                  ].map(([mId, color]) => /* @__PURE__ */ jsxRuntimeExports.jsx(
                                    "marker",
                                    {
                                      id: mId,
                                      viewBox: "0 0 10 10",
                                      refX: "9",
                                      refY: "5",
                                      markerWidth: "6",
                                      markerHeight: "6",
                                      orient: "auto-start-reverse",
                                      children: /* @__PURE__ */ jsxRuntimeExports.jsx("path", { d: "M 0 1.5 L 9 5 L 0 8.5 z", fill: color })
                                    },
                                    mId
                                  )) }),
                                  visibleEdges.map((edge, idx) => {
                                    const p1 = coordsById.get(edge.source);
                                    const p2 = coordsById.get(edge.target);
                                    if (!p1 || !p2) return null;
                                    const x1 = p1.x + CARD_W;
                                    const y1 = p1.y + CARD_H / 2;
                                    const x2 = p2.x;
                                    const y2 = p2.y + CARD_H / 2;
                                    const dx = Math.max(36, Math.abs(x2 - x1) * 0.45);
                                    const sameRowLongSpan = Math.abs(y2 - y1) < 6 && p2.colIdx - p1.colIdx > 1;
                                    const arcLift = sameRowLongSpan ? -30 : 0;
                                    const dStr = `M ${x1.toFixed(1)} ${y1.toFixed(1)} C ${(x1 + dx).toFixed(1)} ${(y1 + arcLift).toFixed(1)}, ${(x2 - dx).toFixed(1)} ${(y2 + arcLift).toFixed(1)}, ${x2.toFixed(1)} ${y2.toFixed(1)}`;
                                    const spec = edgeVisualSpec(edge);
                                    const connected = Boolean(
                                      activeSelectedId && (edge.source === activeSelectedId || edge.target === activeSelectedId)
                                    );
                                    const dimmed = Boolean(activeSelectedId && !connected);
                                    return /* @__PURE__ */ jsxRuntimeExports.jsx(
                                      "path",
                                      {
                                        className: `ag-edge-path ${spec.cls}${connected ? " is-highlighted" : ""}${dimmed ? " is-dimmed" : ""}`,
                                        d: dStr,
                                        markerEnd: `url(#${spec.marker})`,
                                        "data-edge-id": String(edge.id || ""),
                                        "data-source": String(edge.source || ""),
                                        "data-target": String(edge.target || ""),
                                        "data-edge-type": String(edge.primary_type || "")
                                      },
                                      String(edge.id || idx)
                                    );
                                  })
                                ]
                              }
                            ) : null
                          }
                        ),
                        /* @__PURE__ */ jsxRuntimeExports.jsx(
                          "div",
                          {
                            className: "ag-nodes-layer",
                            id: "ag-nodes-layer",
                            role: "list",
                            "aria-label": "Planned action graph nodes",
                            style: nodes.length > 0 ? { width: `${totalWidth}px`, height: `${totalHeight}px` } : void 0,
                            children: nodes.map((node) => {
                              var _a2;
                              const pos = coordsById.get(node.id) || { x: 0, y: 0 };
                              const status = String(node.status || "queued");
                              const statusCls = status === "ready" ? "is-ready" : status === "waiting_resources" ? "is-waiting" : status === "blocked_gate" ? "is-blocked" : "is-queued";
                              const prio = String(node.priority || "P2").toUpperCase();
                              const prioCls = prio === "P0" ? "is-p0" : prio === "P1" ? "is-p1" : prio === "P2" ? "is-p2" : "is-p3";
                              const isSel = Boolean(activeSelectedId && node.id === activeSelectedId);
                              const isUp = Boolean(activeSelectedId && upstreamIds.has(node.id));
                              const isDown = Boolean(activeSelectedId && downstreamIds.has(node.id));
                              const isDim = Boolean(activeSelectedId && !isSel && !isUp && !isDown);
                              const ord = Math.max(
                                1,
                                Math.round(safeFiniteNumber(node.order_index, 0) ?? 0) + 1
                              );
                              const gaStepIdx = safeFiniteNumber((_a2 = node == null ? void 0 : node.ga_step) == null ? void 0 : _a2.step_index, null);
                              return /* @__PURE__ */ jsxRuntimeExports.jsxs(
                                "button",
                                {
                                  type: "button",
                                  className: `ag-node-card ${statusCls}${isSel ? " is-selected" : ""}${!isSel && isUp ? " is-upstream" : ""}${!isSel && isDown ? " is-downstream" : ""}${isDim ? " is-dimmed" : ""}`,
                                  role: "listitem",
                                  "data-node-id": String(node.id),
                                  "data-node-kind": String(node.kind || ""),
                                  "data-node-priority": prio,
                                  "data-node-status": status,
                                  "aria-pressed": isSel ? "true" : "false",
                                  style: { left: `${pos.x}px`, top: `${pos.y}px` },
                                  title: sanitizeMainWindowText(node.reason || ""),
                                  onClick: () => {
                                    if (consumeSuppressClick()) return;
                                    setSelectedNodeId((prev) => prev === node.id ? null : node.id);
                                  },
                                  children: [
                                    /* @__PURE__ */ jsxRuntimeExports.jsxs("div", { className: "ag-node-head", children: [
                                      /* @__PURE__ */ jsxRuntimeExports.jsxs("div", { className: "ag-node-badges", children: [
                                        /* @__PURE__ */ jsxRuntimeExports.jsx("span", { className: "ag-step-tag tabular", children: gaStepIdx !== null ? `#${ord} · G${Math.round(gaStepIdx) + 1}` : `#${ord}` }),
                                        /* @__PURE__ */ jsxRuntimeExports.jsx("span", { className: `ag-prio-tag tabular ${prioCls}`, children: prio })
                                      ] }),
                                      /* @__PURE__ */ jsxRuntimeExports.jsx("span", { className: `ag-state-tag tabular ${statusCls}`, children: formatNodeStatusShort(status) })
                                    ] }),
                                    /* @__PURE__ */ jsxRuntimeExports.jsx("div", { className: "ag-node-title", children: sanitizeMainWindowText(formatNodeTargetLabel(node)) }),
                                    /* @__PURE__ */ jsxRuntimeExports.jsxs("div", { className: "ag-node-foot", children: [
                                      /* @__PURE__ */ jsxRuntimeExports.jsx("span", { className: "ag-node-coords", children: node.planet_coords ? `[${node.planet_coords}]` : "[GLOBAL]" }),
                                      /* @__PURE__ */ jsxRuntimeExports.jsx("span", { className: "ag-node-eta", children: sanitizeMainWindowText(`ETA ${node.total_eta || "Ready"}`) })
                                    ] })
                                  ]
                                },
                                String(node.id)
                              );
                            })
                          }
                        )
                      ]
                    }
                  ),
                  /* @__PURE__ */ jsxRuntimeExports.jsx("div", { className: "ag-empty-state", id: "ag-empty-state", hidden: nodes.length > 0, children: "No planned actions for the current empire snapshot and simulation parameters." })
                ]
              }
            ),
            /* @__PURE__ */ jsxRuntimeExports.jsxs(
              "div",
              {
                className: "ag-inspector-bar",
                id: "ag-node-inspector",
                "aria-label": "Selected action node details",
                children: [
                  /* @__PURE__ */ jsxRuntimeExports.jsxs("div", { className: "ag-insp-main", children: [
                    /* @__PURE__ */ jsxRuntimeExports.jsx("span", { className: "module-code tabular", id: "ag-insp-step", children: selectedObj ? `#${Math.max(1, Math.round(safeFiniteNumber(selectedObj.order_index, 0) ?? 0) + 1)}` : "#—" }),
                    /* @__PURE__ */ jsxRuntimeExports.jsxs("div", { className: "ag-insp-copy", children: [
                      /* @__PURE__ */ jsxRuntimeExports.jsx("strong", { id: "ag-insp-title", children: selectedObj ? sanitizeMainWindowText(
                        `${selectedObj.priority || "P2"} · ${formatNodeTargetLabel(selectedObj)} ${selectedObj.planet_coords ? `[${selectedObj.planet_coords}]` : "[GLOBAL]"}`
                      ) : "Select a node in the graph to inspect dependencies and ETAs" }),
                      /* @__PURE__ */ jsxRuntimeExports.jsx("p", { className: "ag-insp-sub tabular", id: "ag-insp-meta", children: selectedObj ? `Wait: ${String(selectedObj.wait_eta || "Ready")} · Duration: ${String(selectedObj.duration_text || "Instant")} · Total: ${String(selectedObj.total_eta || "Ready")}${selectedObj.ga_step && typeof selectedObj.ga_step === "object" ? ` · GA Step #${Math.round(safeFiniteNumber(selectedObj.ga_step.step_index, 0) ?? 0) + 1} (${Math.round(safeFiniteNumber(selectedObj.ga_step.start_t, 0) ?? 0)}s → ${Math.round(safeFiniteNumber(selectedObj.ga_step.finish_t, 0) ?? 0)}s)` : ""} — ${sanitizeMainWindowText(selectedObj.reason || "Planned empire operation.")}` : "Click any node card above to highlight upstream prerequisites and downstream dependents." })
                    ] })
                  ] }),
                  /* @__PURE__ */ jsxRuntimeExports.jsx("div", { className: "ag-insp-deps tabular", id: "ag-insp-deps", children: selectedObj ? incomingSel.length === 0 && outgoingSel.length === 0 ? /* @__PURE__ */ jsxRuntimeExports.jsx("span", { className: "ag-dep-pill", children: "Independent root action (0 links)" }) : /* @__PURE__ */ jsxRuntimeExports.jsxs(jsxRuntimeExports.Fragment, { children: [
                    incomingSel.slice(0, 4).map((edge, idx) => {
                      const src = nodesById.get(edge.source);
                      const srcNum = src ? Math.round(safeFiniteNumber(src.order_index, 0) ?? 0) + 1 : "?";
                      return /* @__PURE__ */ jsxRuntimeExports.jsx(
                        "span",
                        {
                          className: "ag-dep-pill is-up",
                          title: sanitizeMainWindowText(edge.label || ""),
                          children: sanitizeMainWindowText(
                            `← #${srcNum} ${src ? src.target_name : edge.source} (${edge.primary_type})`
                          )
                        },
                        `in-${idx}`
                      );
                    }),
                    outgoingSel.slice(0, 4).map((edge, idx) => {
                      const dst = nodesById.get(edge.target);
                      const dstNum = dst ? Math.round(safeFiniteNumber(dst.order_index, 0) ?? 0) + 1 : "?";
                      return /* @__PURE__ */ jsxRuntimeExports.jsx(
                        "span",
                        {
                          className: "ag-dep-pill is-down",
                          title: sanitizeMainWindowText(edge.label || ""),
                          children: sanitizeMainWindowText(
                            `→ #${dstNum} ${dst ? dst.target_name : edge.target} (${edge.primary_type})`
                          )
                        },
                        `out-${idx}`
                      );
                    })
                  ] }) : null })
                ]
              }
            )
          ] }),
          /* @__PURE__ */ jsxRuntimeExports.jsxs(
            "aside",
            {
              className: "hud-window ag-control-panel",
              id: "ag-control-panel",
              "aria-label": "Planner parameter simulation and apply controls",
              children: [
                /* @__PURE__ */ jsxRuntimeExports.jsxs("div", { className: "window-bar window-bar-wrap", children: [
                  /* @__PURE__ */ jsxRuntimeExports.jsxs("div", { className: "window-title-group", children: [
                    /* @__PURE__ */ jsxRuntimeExports.jsx("span", { className: "module-code tabular", children: "PRM" }),
                    /* @__PURE__ */ jsxRuntimeExports.jsx("h2", { children: "Planner Parameters" })
                  ] }),
                  /* @__PURE__ */ jsxRuntimeExports.jsx(
                    "span",
                    {
                      className: `ag-dirty-badge tabular ${agSimDirty ? "is-dirty" : "is-synced"}`,
                      id: "ag-dirty-badge",
                      children: agSimDirty ? "MODIFIED (PREVIEW)" : "ACTIVE SYNCED"
                    }
                  )
                ] }),
                /* @__PURE__ */ jsxRuntimeExports.jsxs("div", { className: "ag-panel-tabs", role: "tablist", "aria-label": "Parameter categories", children: [
                  /* @__PURE__ */ jsxRuntimeExports.jsx(
                    "button",
                    {
                      type: "button",
                      className: `tab-btn${paramTab === "core" ? " active" : ""}`,
                      role: "tab",
                      "aria-selected": paramTab === "core" ? "true" : "false",
                      id: "ag-ptab-btn-core",
                      "data-ag-ptab": "core",
                      onClick: () => {
                        syncDraftValuesFromDom();
                        setUiPref("agParamTab", "core");
                      },
                      children: "Mode & Tuning"
                    }
                  ),
                  /* @__PURE__ */ jsxRuntimeExports.jsx(
                    "button",
                    {
                      type: "button",
                      className: `tab-btn${paramTab === "scope" ? " active" : ""}`,
                      role: "tab",
                      "aria-selected": paramTab === "scope" ? "true" : "false",
                      id: "ag-ptab-btn-scope",
                      "data-ag-ptab": "scope",
                      onClick: () => {
                        syncDraftValuesFromDom();
                        setUiPref("agParamTab", "scope");
                      },
                      children: "Automation & Limits"
                    }
                  )
                ] }),
                /* @__PURE__ */ jsxRuntimeExports.jsxs("div", { className: "ag-panel-body", children: [
                  /* @__PURE__ */ jsxRuntimeExports.jsxs("div", { className: "ag-param-pane", id: "ag-param-pane-core", hidden: paramTab !== "core", children: [
                    /* @__PURE__ */ jsxRuntimeExports.jsxs("div", { className: "control-field", children: [
                      /* @__PURE__ */ jsxRuntimeExports.jsx("label", { htmlFor: "ag-cfg-planner_mode", children: "Simulated Planner Mode" }),
                      /* @__PURE__ */ jsxRuntimeExports.jsxs(
                        "select",
                        {
                          id: "ag-cfg-planner_mode",
                          value: draftModeNorm,
                          onChange: (e) => {
                            const v = e.target.value === "ga_nn" ? "ga_nn" : "priority";
                            syncDraftValuesFromDom(v);
                            void runAgSimulation({ explicit: false, modeOverride: v });
                          },
                          children: [
                            /* @__PURE__ */ jsxRuntimeExports.jsx("option", { value: "priority", children: "priority — Rule-Based Heuristic" }),
                            /* @__PURE__ */ jsxRuntimeExports.jsx("option", { value: "ga_nn", children: "ga_nn — GA + Neural Network" })
                          ]
                        }
                      )
                    ] }),
                    /* @__PURE__ */ jsxRuntimeExports.jsxs("div", { className: "control-field", children: [
                      /* @__PURE__ */ jsxRuntimeExports.jsx("label", { htmlFor: "ag-cfg-playstyle", children: "Playstyle Doctrine" }),
                      /* @__PURE__ */ jsxRuntimeExports.jsxs(
                        "select",
                        {
                          id: "ag-cfg-playstyle",
                          value: curPlaystyle,
                          onChange: (e) => {
                            const v = e.target.value;
                            setDraftValues((prev) => ({ ...prev, playstyle: v }));
                            setTimeout(() => runAgSimulation({ explicit: false }), 0);
                          },
                          children: [
                            /* @__PURE__ */ jsxRuntimeExports.jsx("option", { value: "fleeter", children: "fleeter — Combat & Techs" }),
                            /* @__PURE__ */ jsxRuntimeExports.jsx("option", { value: "hybrid", children: "hybrid — Balanced" }),
                            /* @__PURE__ */ jsxRuntimeExports.jsx("option", { value: "miner", children: "miner — Economy ROI" }),
                            !knownPlaystyles.includes(curPlaystyle) && curPlaystyle ? /* @__PURE__ */ jsxRuntimeExports.jsx("option", { value: curPlaystyle, children: curPlaystyle }) : null
                          ]
                        }
                      )
                    ] }),
                    /* @__PURE__ */ jsxRuntimeExports.jsxs(
                      "div",
                      {
                        className: `ag-param-group${draftModeNorm !== "ga_nn" ? " is-muted" : ""}`,
                        id: "ag-ga-params-group",
                        children: [
                          /* @__PURE__ */ jsxRuntimeExports.jsx("h3", { children: "GA + Neural Optimizer" }),
                          /* @__PURE__ */ jsxRuntimeExports.jsxs("div", { className: "ag-param-grid", children: [
                            [
                              ["ga_population_size", "Population (4–200)", 4, 200, 1],
                              ["ga_generations", "GA Generations (1–200)", 1, 200, 1],
                              ["ga_horizon_steps", "Horizon Steps (1–24)", 1, 24, 1],
                              ["ga_mutation_rate", "Mutation Rate (0–1)", 0, 1, 0.05],
                              ["ga_nn_weight", "Neural Weight (0–1)", 0, 1, 0.05]
                            ].map(([key, label, minV, maxV, stepV]) => /* @__PURE__ */ jsxRuntimeExports.jsxs("div", { className: "control-field", children: [
                              /* @__PURE__ */ jsxRuntimeExports.jsx("label", { htmlFor: `ag-cfg-${key}`, children: label }),
                              /* @__PURE__ */ jsxRuntimeExports.jsx(
                                "input",
                                {
                                  type: "number",
                                  id: `ag-cfg-${key}`,
                                  min: minV,
                                  max: maxV,
                                  step: stepV,
                                  value: String(draftValues[key] ?? AG_NUM_DEFAULTS[key]),
                                  onChange: (e) => {
                                    setDraftValues((prev) => ({ ...prev, [key]: e.target.value }));
                                    scheduleDebouncedSim();
                                  }
                                }
                              )
                            ] }, key)),
                            /* @__PURE__ */ jsxRuntimeExports.jsxs("div", { className: "control-field", children: [
                              /* @__PURE__ */ jsxRuntimeExports.jsx("label", { htmlFor: "ag-cfg-ga_seed", children: "GA Seed (Blank = Auto)" }),
                              /* @__PURE__ */ jsxRuntimeExports.jsx(
                                "input",
                                {
                                  type: "text",
                                  inputMode: "numeric",
                                  id: "ag-cfg-ga_seed",
                                  placeholder: "Auto",
                                  value: String(draftValues.ga_seed ?? ""),
                                  onChange: (e) => {
                                    setDraftValues((prev) => ({ ...prev, ga_seed: e.target.value }));
                                    scheduleDebouncedSim();
                                  }
                                }
                              )
                            ] })
                          ] })
                        ]
                      }
                    ),
                    /* @__PURE__ */ jsxRuntimeExports.jsxs("div", { className: "ag-param-group", id: "ag-heuristic-params-group", children: [
                      /* @__PURE__ */ jsxRuntimeExports.jsx("h3", { children: "Economy & Safety Thresholds" }),
                      /* @__PURE__ */ jsxRuntimeExports.jsx("div", { className: "ag-param-grid", children: [
                        ["storage_warn_ratio", "Storage Warn Ratio", 0.1, 0.99, 0.05],
                        ["blast_pig_iron_max_share", "Max b2 Pig Share", 0.1, 1, 0.05],
                        ["combat_safety_margin", "Combat Margin (≥1.0)", 1, 1e3, 0.1],
                        ["raid_min_loot_spice_eq", "Min Raid Loot (SpEq)", 0, void 0, 100],
                        ["hot_planet_solar_temp_threshold", "Solar Temp (°C)", -273, 1e3, 5],
                        ["min_free_fields_warning", "Min Free Fields", 1, 1e3, 1],
                        ["min_colony_fields", "Min Colony Fields", 50, 600, 10]
                      ].map(([key, label, minV, maxV, stepV]) => /* @__PURE__ */ jsxRuntimeExports.jsxs("div", { className: "control-field", children: [
                        /* @__PURE__ */ jsxRuntimeExports.jsx("label", { htmlFor: `ag-cfg-${key}`, children: label }),
                        /* @__PURE__ */ jsxRuntimeExports.jsx(
                          "input",
                          {
                            type: "number",
                            id: `ag-cfg-${key}`,
                            min: minV,
                            max: maxV,
                            step: stepV,
                            value: String(draftValues[key] ?? AG_NUM_DEFAULTS[key]),
                            onChange: (e) => {
                              setDraftValues((prev) => ({ ...prev, [key]: e.target.value }));
                              scheduleDebouncedSim();
                            }
                          }
                        )
                      ] }, key)) })
                    ] })
                  ] }),
                  /* @__PURE__ */ jsxRuntimeExports.jsxs("div", { className: "ag-param-pane", id: "ag-param-pane-scope", hidden: paramTab !== "scope", children: [
                    /* @__PURE__ */ jsxRuntimeExports.jsx("div", { className: "ag-param-grid", children: [
                      ["fleet_save_fuel_reserve_spice", "Escape Fuel Reserve", 0, void 0, 50],
                      ["action_batch_interval_seconds", "Batch & Refresh Interval (s)", 5, 86400, 5],
                      ["galaxy_scan_click_delay_seconds", "Galaxy Scan Click Delay (s)", 0.2, 60, 0.1],
                      ["galaxy_scan_click_jitter_seconds", "Galaxy Scan Jitter (s)", 0, 30, 0.1],
                      ["max_actions_per_hour", "Max Actions / Hour", 1, 1e5, 1],
                      ["reserved_spy_slots", "Reserved Spy Slots", 0, 10, 1]
                    ].map(([key, label, minV, maxV, stepV]) => /* @__PURE__ */ jsxRuntimeExports.jsxs("div", { className: "control-field", children: [
                      /* @__PURE__ */ jsxRuntimeExports.jsx("label", { htmlFor: `ag-cfg-${key}`, children: label }),
                      /* @__PURE__ */ jsxRuntimeExports.jsx(
                        "input",
                        {
                          type: "number",
                          id: `ag-cfg-${key}`,
                          min: minV,
                          max: maxV,
                          step: stepV,
                          value: String(draftValues[key] ?? AG_NUM_DEFAULTS[key]),
                          onChange: (e) => {
                            setDraftValues((prev) => ({ ...prev, [key]: e.target.value }));
                            scheduleDebouncedSim();
                          }
                        }
                      )
                    ] }, key)) }),
                    /* @__PURE__ */ jsxRuntimeExports.jsx("div", { className: "ag-toggles-stack", children: [
                      ["auto_throttle_blast_furnace", "Auto-pause Blast furnace (0%)"],
                      ["merchant_mode", "Merchant Mode (Skip b2)"],
                      ["allow_research", "Auto Research"],
                      ["allow_shipyard", "Auto Shipyard"],
                      ["allow_multi_planet", "Multi-Planet Planning"],
                      ["allow_fleetsave", "P0 Auto Escape Flight"],
                      ["allow_galaxy_scan", "Galaxy Scanner"],
                      ["allow_raids", "Auto Raids (Inactive/Spacia)"],
                      ["allow_pvp_in_az", "Allow PvP inside AZ"],
                      ["allow_colonize", "Auto Colonize"],
                      ["allow_transmitter", "Daily Free Transmitter"],
                      ["forbid_planetary_solar", "Forbid Solar on Planets (Asteroid Only)"]
                    ].map(([bKey, label]) => /* @__PURE__ */ jsxRuntimeExports.jsxs("label", { className: "toggle-chip", children: [
                      /* @__PURE__ */ jsxRuntimeExports.jsx(
                        "input",
                        {
                          type: "checkbox",
                          id: `ag-cfg-${bKey}`,
                          checked: Boolean(draftValues[bKey]),
                          onChange: (e) => {
                            const checked = e.target.checked;
                            setDraftValues((prev) => ({ ...prev, [bKey]: checked }));
                            setTimeout(() => runAgSimulation({ explicit: false }), 0);
                          }
                        }
                      ),
                      " ",
                      label
                    ] }, bKey)) })
                  ] })
                ] }),
                /* @__PURE__ */ jsxRuntimeExports.jsxs("div", { className: "ag-panel-footer", children: [
                  /* @__PURE__ */ jsxRuntimeExports.jsx("p", { className: "ag-sim-note", id: "ag-sim-note", children: agSimDirty ? "Preview mode active: active StrategyConfig and config.yaml remain unchanged until applied." : "Edits simulate a preview graph without modifying active config.yaml until applied." }),
                  /* @__PURE__ */ jsxRuntimeExports.jsxs("div", { className: "ag-footer-actions", children: [
                    /* @__PURE__ */ jsxRuntimeExports.jsx(
                      "button",
                      {
                        type: "button",
                        className: "btn btn-secondary btn-sm",
                        id: "btn-ag-simulate",
                        onClick: () => runAgSimulation({ explicit: true }),
                        children: "Simulate Graph"
                      }
                    ),
                    /* @__PURE__ */ jsxRuntimeExports.jsx(
                      "button",
                      {
                        type: "button",
                        className: "btn btn-secondary btn-sm",
                        id: "btn-ag-reset",
                        onClick: onResetSim,
                        children: "Reset to Active"
                      }
                    ),
                    /* @__PURE__ */ jsxRuntimeExports.jsx(
                      "button",
                      {
                        type: "button",
                        className: "btn btn-primary btn-sm btn-span",
                        id: "btn-ag-apply",
                        onClick: onApplySim,
                        children: "Apply to Active Config"
                      }
                    )
                  ] })
                ] })
              ]
            }
          )
        ]
      }
    );
  };
  const BuildingInspector = ({
    etaData,
    selectedRow: selected,
    bfPct
  }) => {
    var _a, _b;
    const {
      apiCommand: apiCommand2,
      loadDashboard,
      showFeedback,
      enqueueManualCommand,
      toggleBlastFurnaceRate
    } = useConsole();
    if (!selected) {
      return /* @__PURE__ */ jsxRuntimeExports.jsxs("aside", { className: "inspector-pane", id: "bld-inspector", "aria-label": "Selected level forecast", children: [
        /* @__PURE__ */ jsxRuntimeExports.jsxs("div", { className: "inspector-head", children: [
          /* @__PURE__ */ jsxRuntimeExports.jsx("span", { className: "module-code tabular", id: "insp-key", children: etaData ? "—" : "B1" }),
          /* @__PURE__ */ jsxRuntimeExports.jsxs("div", { children: [
            /* @__PURE__ */ jsxRuntimeExports.jsx("h3", { id: "insp-title", children: etaData ? "Select a Building or Tech" : "Select a Building or Research" }),
            /* @__PURE__ */ jsxRuntimeExports.jsx("p", { className: "action-sub tabular", id: "insp-subtitle", children: etaData ? "Click any row on the left." : "Click any row to inspect L+1 to L+4 arrival estimates" })
          ] })
        ] }),
        /* @__PURE__ */ jsxRuntimeExports.jsx("div", { className: "inspector-stats tabular", id: "insp-meta-box", children: /* @__PURE__ */ jsxRuntimeExports.jsx("span", { children: etaData ? "No data." : "No item selected." }) }),
        /* @__PURE__ */ jsxRuntimeExports.jsx("div", { className: "inspector-bf-hint", id: "insp-bf-hint", hidden: true }),
        /* @__PURE__ */ jsxRuntimeExports.jsx("div", { className: "table-wrap", children: /* @__PURE__ */ jsxRuntimeExports.jsxs("table", { children: [
          /* @__PURE__ */ jsxRuntimeExports.jsx("thead", { children: /* @__PURE__ */ jsxRuntimeExports.jsxs("tr", { children: [
            /* @__PURE__ */ jsxRuntimeExports.jsx("th", { children: "Target" }),
            /* @__PURE__ */ jsxRuntimeExports.jsx("th", { children: "Cost" }),
            /* @__PURE__ */ jsxRuntimeExports.jsx("th", { className: "col-right", children: "Build" }),
            /* @__PURE__ */ jsxRuntimeExports.jsx("th", { className: "col-right", children: "Reached" })
          ] }) }),
          /* @__PURE__ */ jsxRuntimeExports.jsx("tbody", { id: "insp-horizons-tbody", children: !etaData ? /* @__PURE__ */ jsxRuntimeExports.jsx("tr", { children: /* @__PURE__ */ jsxRuntimeExports.jsx("td", { colSpan: 4, className: "empty-cell", children: "Select a row for multi-level schedule." }) }) : null })
        ] }) }),
        /* @__PURE__ */ jsxRuntimeExports.jsxs("div", { className: "inspector-actions", children: [
          /* @__PURE__ */ jsxRuntimeExports.jsx("button", { type: "button", className: "btn btn-primary btn-sm", id: "btn-insp-build", hidden: true, children: "Queue Next Level Live" }),
          /* @__PURE__ */ jsxRuntimeExports.jsx(
            "button",
            {
              type: "button",
              className: "btn btn-secondary btn-sm",
              id: "btn-insp-pin-queue",
              hidden: true,
              children: "+ Pin to Queue (P1)"
            }
          ),
          /* @__PURE__ */ jsxRuntimeExports.jsx(
            "button",
            {
              type: "button",
              className: "btn btn-secondary btn-sm btn-danger-ghost",
              id: "btn-insp-demolish",
              hidden: true,
              children: "Demolish"
            }
          )
        ] })
      ] });
    }
    const keyStr = String(selected.key || "");
    const isResearch = keyStr.startsWith("r");
    const isTurret = Boolean(selected.is_defense_turret);
    const curLvl = finiteNum(selected.current_level, 0);
    const nextLvl = finiteNum(selected.next_level ?? selected.target_level, null);
    const unmet = Array.isArray(selected.unmet_prerequisites) ? selected.unmet_prerequisites : [];
    const pEcon = (etaData == null ? void 0 : etaData.planet_economy) || {};
    const planetId = (_a = etaData == null ? void 0 : etaData.planet) == null ? void 0 : _a.planet_id;
    const selBnTag = typeof selected.bottleneck === "string" && selected.bottleneck.trim() ? bottleneckTag(selected.bottleneck) : "";
    const metaLines = isTurret ? [
      `Stationed Units: ${fmtInt(curLvl)}`,
      `Unit Cost: ${formatResourcesInline(selected.cost)}`,
      `Combat Stats: ATK ${fmtInt(selected.attack ?? 0)} · SHD ${fmtInt(selected.shield ?? 0)} · Hull ${fmtInt(selected.hull ?? 0)}`
    ] : [
      `Prerequisites: ${unmet.length === 0 ? "Unlocked" : unmet.join(", ")}`,
      `Next L${selected.next_level ?? "—"} Cost: ${formatResourcesInline(selected.cost)}`,
      `Resources Ready: ${selected.wait_eta ?? "—"}${selBnTag ? ` (${selBnTag} bottleneck)` : ""}`,
      `Estimated Level Reached: ${selected.reach_eta ?? "—"}`
    ];
    if (!isResearch && !isTurret && selected.energy_delta) {
      metaLines.push(
        `Energy Impact: ${selected.energy_delta >= 0 ? "+" : ""}${selected.energy_delta} E`
      );
    }
    const bfUsed = finiteNum(pEcon.blast_furnace_pig_used_per_hr, 0);
    const econBfPct = finiteNum(pEcon.blast_furnace_pct, 100);
    const missPig = finiteNum((_b = selected.missing) == null ? void 0 : _b.pig_iron, 0);
    const showBfHint = selected.bottleneck === "pig_iron" && econBfPct > 0 && bfUsed > 0 && missPig > 0;
    let bfHintText = "";
    if (showBfHint) {
      const newRate = finiteNum(pEcon.pig_iron_per_hr, 0) + bfUsed;
      const oldWait = selected.wait_eta ?? "—";
      const altHours = newRate > 0 ? missPig / newRate : NaN;
      const altWait = Number.isFinite(altHours) ? `${altHours.toFixed(1)}h` : "Blocked";
      bfHintText = `Bucket tip: Pausing Blast furnace (b2 → 0%) frees +${fmtInt(Math.round(bfUsed))} Pig/h (Pig iron wait improves from ${oldWait} to ${altWait}).`;
    }
    const horizons = Array.isArray(selected.horizons) ? selected.horizons : [];
    const canQueueLive = isTurret ? false : Boolean(isResearch ? selected.can_research_live : selected.can_build_live);
    const unitId = isResearch ? selected.research_id : selected.building_id;
    const canPinQueue = !isTurret && unitId != null && nextLvl !== null && nextLvl > 0;
    const canDemolish = !isResearch && !isTurret && unitId != null && curLvl > 0;
    const handleBuildLive = async () => {
      try {
        showFeedback(`Submitting ${selected.name} L${selected.next_level}...`, "ok");
        const cmd = isResearch ? {
          command: "research",
          research_id: selected.research_id,
          planet_id: planetId,
          dry_run: false
        } : {
          command: "build",
          building_id: selected.building_id,
          planet_id: planetId,
          dry_run: false
        };
        const res = await apiCommand2(cmd);
        showFeedback(res.message || "Queued.", "ok");
        await loadDashboard({ refresh: false, force: true });
      } catch (err) {
        showFeedback(`Build failed: ${err.message}`, feedbackKindForError(err));
      }
    };
    const handlePinToQueue = () => {
      if (!canPinQueue || nextLvl === null) return;
      void enqueueManualCommand({
        kind: isResearch ? "RESEARCH" : "BUILD",
        priority: "P1",
        position: "top",
        planet_id: planetId || 0,
        target_id: unitId,
        target_name: selected.name || "",
        target_level: nextLvl,
        deduplicate_pending: true
      });
    };
    const handleDemolish = async () => {
      if (!canDemolish) return;
      try {
        showFeedback(`Demolishing ${selected.name} L${curLvl} → L${curLvl - 1}...`, "ok");
        const res = await apiCommand2({
          command: "demolish_building",
          building_id: selected.building_id,
          planet_id: planetId,
          dry_run: false
        });
        showFeedback(res.message || `Demolish submitted for ${selected.name}.`, "ok");
        await loadDashboard({ refresh: false, force: true });
      } catch (err) {
        showFeedback(`Demolish failed: ${err.message}`, feedbackKindForError(err));
      }
    };
    return /* @__PURE__ */ jsxRuntimeExports.jsxs("aside", { className: "inspector-pane", id: "bld-inspector", "aria-label": "Selected level forecast", children: [
      /* @__PURE__ */ jsxRuntimeExports.jsxs("div", { className: "inspector-head", children: [
        /* @__PURE__ */ jsxRuntimeExports.jsx("span", { className: "module-code tabular", id: "insp-key", children: keyStr ? keyStr.toUpperCase() : "UNIT" }),
        /* @__PURE__ */ jsxRuntimeExports.jsxs("div", { children: [
          /* @__PURE__ */ jsxRuntimeExports.jsx("h3", { id: "insp-title", children: selected.name || "Unit" }),
          /* @__PURE__ */ jsxRuntimeExports.jsx("p", { className: "action-sub tabular", id: "insp-subtitle", children: isTurret ? `Owned ${fmtInt(curLvl)} · Planetary Defense` : `Current L${curLvl} · Next L${selected.next_level ?? "—"} · Build time ${selected.build_time_text ?? "—"}` })
        ] })
      ] }),
      /* @__PURE__ */ jsxRuntimeExports.jsx("div", { className: "inspector-stats tabular", id: "insp-meta-box", children: metaLines.map((line, i) => /* @__PURE__ */ jsxRuntimeExports.jsx("span", { children: line }, i)) }),
      selected.building_id === 2 ? /* @__PURE__ */ jsxRuntimeExports.jsxs("div", { className: "inline-actions", "aria-label": "Blast furnace throttle presets", children: [
        /* @__PURE__ */ jsxRuntimeExports.jsx("span", { className: "action-sub tabular", children: `B2 Throttle (${bfPct}%):` }),
        /* @__PURE__ */ jsxRuntimeExports.jsx(
          "button",
          {
            type: "button",
            className: "btn btn-warning btn-sm",
            disabled: bfPct === 0,
            onClick: () => toggleBlastFurnaceRate(0),
            children: "0%"
          }
        ),
        /* @__PURE__ */ jsxRuntimeExports.jsx(
          "button",
          {
            type: "button",
            className: "btn btn-secondary btn-sm",
            disabled: bfPct === 50,
            onClick: () => toggleBlastFurnaceRate(50),
            children: "50%"
          }
        ),
        /* @__PURE__ */ jsxRuntimeExports.jsx(
          "button",
          {
            type: "button",
            className: "btn btn-secondary btn-sm",
            disabled: bfPct === 100,
            onClick: () => toggleBlastFurnaceRate(100),
            children: "100%"
          }
        )
      ] }) : null,
      /* @__PURE__ */ jsxRuntimeExports.jsx("div", { className: "inspector-bf-hint", id: "insp-bf-hint", hidden: !showBfHint, children: bfHintText }),
      /* @__PURE__ */ jsxRuntimeExports.jsx("div", { className: "table-wrap", children: /* @__PURE__ */ jsxRuntimeExports.jsxs("table", { children: [
        /* @__PURE__ */ jsxRuntimeExports.jsx("thead", { children: /* @__PURE__ */ jsxRuntimeExports.jsxs("tr", { children: [
          /* @__PURE__ */ jsxRuntimeExports.jsx("th", { children: "Target" }),
          /* @__PURE__ */ jsxRuntimeExports.jsx("th", { children: "Cost" }),
          /* @__PURE__ */ jsxRuntimeExports.jsx("th", { className: "col-right", children: "Build" }),
          /* @__PURE__ */ jsxRuntimeExports.jsx("th", { className: "col-right", children: "Reached" })
        ] }) }),
        /* @__PURE__ */ jsxRuntimeExports.jsx("tbody", { id: "insp-horizons-tbody", children: horizons.map((h, idx) => /* @__PURE__ */ jsxRuntimeExports.jsxs("tr", { children: [
          /* @__PURE__ */ jsxRuntimeExports.jsx("td", { className: "tabular", children: `L${h.level}` }),
          /* @__PURE__ */ jsxRuntimeExports.jsx("td", { className: "tabular", children: formatResourcesInline(h.cost) }),
          /* @__PURE__ */ jsxRuntimeExports.jsx("td", { className: "col-right tabular", children: h.build_time_text }),
          /* @__PURE__ */ jsxRuntimeExports.jsx("td", { className: "col-right tabular", children: /* @__PURE__ */ jsxRuntimeExports.jsx("span", { className: `eta-pill${h.reach_eta === "—" ? " is-blocked" : ""}`, children: h.reach_eta }) })
        ] }, `hz-${h.level ?? idx}`)) })
      ] }) }),
      /* @__PURE__ */ jsxRuntimeExports.jsxs("div", { className: "inspector-actions", children: [
        /* @__PURE__ */ jsxRuntimeExports.jsx(
          "button",
          {
            type: "button",
            className: "btn btn-primary btn-sm",
            id: "btn-insp-build",
            hidden: !canQueueLive,
            onClick: handleBuildLive,
            children: isResearch ? `Start Research L${selected.next_level}` : `Queue L${selected.next_level} Live`
          }
        ),
        /* @__PURE__ */ jsxRuntimeExports.jsx(
          "button",
          {
            type: "button",
            className: "btn btn-secondary btn-sm",
            id: "btn-insp-pin-queue",
            hidden: !canPinQueue,
            onClick: handlePinToQueue,
            children: nextLvl !== null ? `+ Pin L${nextLvl} to Queue (P1)` : "+ Pin to Queue (P1)"
          }
        ),
        /* @__PURE__ */ jsxRuntimeExports.jsx(
          "button",
          {
            type: "button",
            className: "btn btn-secondary btn-sm btn-danger-ghost",
            id: "btn-insp-demolish",
            hidden: !canDemolish,
            onClick: handleDemolish,
            children: `Demolish L${curLvl} → L${Math.max(0, curLvl - 1)}`
          }
        )
      ] })
    ] });
  };
  const BUILDING_CATEGORY_TABS = [
    { id: "all", bcat: "buildings", label: "All 21" },
    { id: "mines", bcat: "mines", label: "Mines" },
    { id: "energy", bcat: "energy", label: "Energy" },
    { id: "infra", bcat: "infra", label: "Infra" },
    { id: "storage", bcat: "storage", label: "Storage" },
    { id: "special", bcat: "special", label: "Special" },
    { id: "defense", bcat: "defense", label: "Defense" },
    { id: "research", bcat: "research", label: "Research r1–15" },
    { id: "universe_research", bcat: "universe_research", label: "Universe Max Research" }
  ];
  const BuildingsView = () => {
    const {
      dashboard,
      activePage,
      selectedPlanetId,
      activePlanet,
      showFeedback,
      apiCommand: apiCommand2,
      loadDashboard,
      uiPrefs,
      setUiPref,
      buildingsEtaData,
      refreshBuildingsEtaForSelectedPlanet,
      combatData,
      toggleBlastFurnaceRate,
      enqueueManualCommand
    } = useConsole();
    const [syncingUniverseMax, setSyncingUniverseMax] = reactExports.useState(false);
    const etaData = reactExports.useMemo(() => {
      var _a;
      if (buildingsEtaData && activePlanet && ((_a = buildingsEtaData.planet) == null ? void 0 : _a.planet_id) === activePlanet.planet_id) {
        return buildingsEtaData;
      }
      return (dashboard == null ? void 0 : dashboard.buildings_eta) || null;
    }, [activePlanet, buildingsEtaData, dashboard == null ? void 0 : dashboard.buildings_eta]);
    const cmbData = reactExports.useMemo(() => {
      if (combatData && activePlanet && combatData.planet_id === activePlanet.planet_id) {
        return combatData;
      }
      return (dashboard == null ? void 0 : dashboard.combat) || null;
    }, [activePlanet, combatData, dashboard == null ? void 0 : dashboard.combat]);
    const rawCategory = uiPrefs.bldCategory || "all";
    const bldCategory = rawCategory === "buildings" ? "all" : rawCategory;
    const isUniverseResearchTab = bldCategory === "universe_research";
    const isResearchTab = bldCategory === "research";
    const isDefenseTab = bldCategory === "defense";
    const p = (etaData == null ? void 0 : etaData.planet) || {};
    const econ = (etaData == null ? void 0 : etaData.planet_economy) || {};
    const bldDiv = finiteNum(p.building_time_divisor, 1);
    const planetMetaText = etaData ? `[${p.coords || "--"}] · Dev L${finiteNum(p.dev_center_level, 0)} · Lab L${finiteNum(p.research_lab_level, 0)} · Micro L${finiteNum(p.microsystem_level, 0)} · Div x${bldDiv}` : "[--] · Dev L0 · Lab L0 · x1";
    const econBfPct = finiteNum(econ.blast_furnace_pct, null);
    const bfPct = econBfPct !== null ? clampPct$1(Math.round(econBfPct)) : getSelectedBlastFurnacePct(dashboard, selectedPlanetId, buildingsEtaData);
    const defenseRows = reactExports.useMemo(() => {
      const blds = Array.isArray(etaData == null ? void 0 : etaData.buildings) ? etaData.buildings : [];
      const defBuildings = blds.filter(
        (b) => (b == null ? void 0 : b.building_id) === 15 || (b == null ? void 0 : b.building_id) === 16 || (b == null ? void 0 : b.category) === "defense"
      );
      const turrets = Array.isArray(cmbData == null ? void 0 : cmbData.defenses) ? cmbData.defenses.map((d) => ({
        key: d.key,
        unit_id: d.unit_id,
        name: d.name,
        category: "defense",
        is_defense_turret: true,
        current_level: finiteNum(d.owned, 0),
        next_level: finiteNum(d.owned, 0) + 1,
        cost: d.base_cost || {},
        missing: {},
        missing_total: 0,
        build_time_text: "Shipyard",
        wait_eta: "—",
        reach_eta: `${fmtInt(d.owned ?? 0)} owned`,
        wait_hours: 0,
        attack: d.attack,
        shield: d.shield,
        hull: d.hull,
        horizons: []
      })) : [];
      return [...defBuildings, ...turrets];
    }, [cmbData, etaData]);
    const visibleRows = reactExports.useMemo(() => {
      if (!etaData) return [];
      if (isResearchTab) {
        return Array.isArray(etaData.researches) ? etaData.researches : [];
      }
      if (isDefenseTab) {
        return defenseRows;
      }
      const allBlds = Array.isArray(etaData.buildings) ? etaData.buildings : [];
      if (bldCategory === "all") {
        return allBlds;
      }
      return allBlds.filter((b) => (b == null ? void 0 : b.category) === bldCategory);
    }, [bldCategory, defenseRows, etaData, isDefenseTab, isResearchTab]);
    const allInspectableRows = reactExports.useMemo(() => {
      if (!etaData) return [];
      return [
        ...Array.isArray(etaData.buildings) ? etaData.buildings : [],
        ...Array.isArray(etaData.researches) ? etaData.researches : [],
        ...defenseRows.filter((d) => d.is_defense_turret)
      ];
    }, [defenseRows, etaData]);
    const selectedUnitKey = uiPrefs.selectedUnitKey || "b1";
    const selectedRow = reactExports.useMemo(() => {
      if (!etaData) return null;
      const hit = allInspectableRows.find((r) => (r == null ? void 0 : r.key) === selectedUnitKey);
      if (hit) return hit;
      return visibleRows[0] || allInspectableRows[0] || null;
    }, [allInspectableRows, etaData, selectedUnitKey, visibleRows]);
    const universeMaxRows = reactExports.useMemo(
      () => Array.isArray(dashboard == null ? void 0 : dashboard.universe_research_max) ? dashboard.universe_research_max : [],
      [dashboard == null ? void 0 : dashboard.universe_research_max]
    );
    const handleSyncEconomy = reactExports.useCallback(async () => {
      try {
        await apiCommand2({ command: "navigate", action: "internalEconomy", return_home: true });
        showFeedback("Synced live internalEconomy production percentages.", "ok");
        await loadDashboard({ refresh: true, force: true });
        await refreshBuildingsEtaForSelectedPlanet(false);
      } catch (err) {
        showFeedback(`Sync failed: ${err.message}`, feedbackKindForError(err));
      }
    }, [apiCommand2, loadDashboard, refreshBuildingsEtaForSelectedPlanet, showFeedback]);
    const handleRefreshUniverseResearch = reactExports.useCallback(async () => {
      setSyncingUniverseMax(true);
      try {
        showFeedback("Refreshing Universe Max Research levels...", "ok");
        const res = await apiCommand2({ command: "universe_research_max", sync: true });
        showFeedback(
          res.message || "Universe Max Research table refreshed and saved to SQLite.",
          "ok"
        );
        await loadDashboard({ refresh: false, force: true });
      } catch (err) {
        showFeedback(`Universe research sync failed: ${err.message}`, feedbackKindForError(err));
      } finally {
        setSyncingUniverseMax(false);
      }
    }, [apiCommand2, loadDashboard, showFeedback]);
    const handleRowLiveBuild = reactExports.useCallback(
      async (ev, row, rowIsRes) => {
        ev.stopPropagation();
        const cmd = rowIsRes ? {
          command: "research",
          research_id: row.research_id,
          planet_id: p.planet_id,
          dry_run: false
        } : {
          command: "build",
          building_id: row.building_id,
          planet_id: p.planet_id,
          dry_run: false
        };
        try {
          const res = await apiCommand2(cmd);
          showFeedback(res.message || "Queued.", "ok");
          await loadDashboard({ refresh: false, force: true });
        } catch (err) {
          showFeedback(`Failed: ${err.message}`, feedbackKindForError(err));
        }
      },
      [apiCommand2, loadDashboard, p.planet_id, showFeedback]
    );
    const handleRowPinQueue = reactExports.useCallback(
      (ev, row, rowIsRes) => {
        ev.stopPropagation();
        const unitId = rowIsRes ? row.research_id : row.building_id;
        const nextLvl = finiteNum(row.next_level ?? row.target_level, null);
        if (unitId == null || nextLvl === null || nextLvl <= 0) return;
        void enqueueManualCommand({
          kind: rowIsRes ? "RESEARCH" : "BUILD",
          priority: "P1",
          position: "top",
          planet_id: p.planet_id || 0,
          target_id: unitId,
          target_name: row.name || "",
          target_level: nextLvl,
          deduplicate_pending: true
        });
      },
      [enqueueManualCommand, p.planet_id]
    );
    const handleRowDemolishB6 = reactExports.useCallback(
      async (ev, row) => {
        ev.stopPropagation();
        try {
          const curLvl = finiteNum(row.current_level, 0);
          showFeedback(`Demolishing ${row.name || "Thermosolar plant"} L${curLvl}...`, "ok");
          const res = await apiCommand2({
            command: "demolish_building",
            building_id: row.building_id,
            planet_id: p.planet_id,
            dry_run: false
          });
          showFeedback(res.message || "Demolish queued.", "ok");
          await loadDashboard({ refresh: false, force: true });
        } catch (err) {
          showFeedback(`Demolish failed: ${err.message}`, feedbackKindForError(err));
        }
      },
      [apiCommand2, loadDashboard, p.planet_id, showFeedback]
    );
    return /* @__PURE__ */ jsxRuntimeExports.jsx(
      "section",
      {
        id: "view-buildings",
        className: "page-view",
        "aria-label": "Buildings and reach ETAs",
        hidden: activePage !== "buildings",
        children: /* @__PURE__ */ jsxRuntimeExports.jsxs("article", { className: "hud-window page-fill", children: [
          /* @__PURE__ */ jsxRuntimeExports.jsxs("div", { className: "window-bar window-bar-wrap", children: [
            /* @__PURE__ */ jsxRuntimeExports.jsxs("div", { className: "window-title-group", children: [
              /* @__PURE__ */ jsxRuntimeExports.jsx("span", { className: "module-code tabular", children: "BLD" }),
              /* @__PURE__ */ jsxRuntimeExports.jsx("h2", { children: "Buildings & Research — Level Reach Estimates" }),
              /* @__PURE__ */ jsxRuntimeExports.jsx("span", { className: "module-meta tabular", id: "bld-planet-meta", children: planetMetaText })
            ] }),
            /* @__PURE__ */ jsxRuntimeExports.jsxs("div", { className: "filter-toolbar", children: [
              /* @__PURE__ */ jsxRuntimeExports.jsx(
                "div",
                {
                  className: "tab-group",
                  id: "bld-category-filters",
                  role: "group",
                  "aria-label": "Building category filters",
                  children: BUILDING_CATEGORY_TABS.map((tab) => {
                    const isActive = bldCategory === tab.id;
                    return /* @__PURE__ */ jsxRuntimeExports.jsx(
                      "button",
                      {
                        type: "button",
                        className: `chip-btn${isActive ? " active" : ""}`,
                        "data-bld-cat": tab.id,
                        "data-bcat": tab.bcat,
                        onClick: () => setUiPref("bldCategory", tab.id),
                        children: tab.label
                      },
                      tab.id
                    );
                  })
                }
              ),
              /* @__PURE__ */ jsxRuntimeExports.jsxs("div", { className: "inline-actions", children: [
                /* @__PURE__ */ jsxRuntimeExports.jsx(
                  "button",
                  {
                    type: "button",
                    className: "btn btn-warning btn-sm",
                    id: "btn-b2-pause",
                    title: "Set Blast furnace to 0% to boost Pig iron production",
                    disabled: Boolean(etaData) && bfPct === 0,
                    onClick: () => toggleBlastFurnaceRate(0),
                    children: "Blast Furnace 0% (+Pig)"
                  }
                ),
                /* @__PURE__ */ jsxRuntimeExports.jsx(
                  "button",
                  {
                    type: "button",
                    className: "btn btn-secondary btn-sm",
                    id: "btn-b2-half",
                    title: "Set Blast furnace smelting to 50%",
                    disabled: Boolean(etaData) && bfPct === 50,
                    onClick: () => toggleBlastFurnaceRate(50),
                    children: "50%"
                  }
                ),
                /* @__PURE__ */ jsxRuntimeExports.jsx(
                  "button",
                  {
                    type: "button",
                    className: "btn btn-secondary btn-sm",
                    id: "btn-b2-resume",
                    title: "Resume Blast furnace smelting to 100%",
                    disabled: Boolean(etaData) && bfPct === 100,
                    onClick: () => toggleBlastFurnaceRate(100),
                    children: "100% (Metal)"
                  }
                ),
                /* @__PURE__ */ jsxRuntimeExports.jsx(
                  "button",
                  {
                    type: "button",
                    className: "btn btn-secondary btn-sm",
                    id: "btn-sync-economy",
                    title: "Read internalEconomy select rates from Chrome",
                    onClick: handleSyncEconomy,
                    children: "Sync %"
                  }
                )
              ] })
            ] })
          ] }),
          /* @__PURE__ */ jsxRuntimeExports.jsxs("div", { className: "split-workspace", hidden: isUniverseResearchTab, children: [
            /* @__PURE__ */ jsxRuntimeExports.jsx("div", { className: "table-wrap table-scroll", children: /* @__PURE__ */ jsxRuntimeExports.jsxs("table", { id: "bld-table", children: [
              /* @__PURE__ */ jsxRuntimeExports.jsx("thead", { children: /* @__PURE__ */ jsxRuntimeExports.jsxs("tr", { children: [
                /* @__PURE__ */ jsxRuntimeExports.jsx("th", { children: "Unit" }),
                /* @__PURE__ */ jsxRuntimeExports.jsx("th", { className: "col-right", children: "Lvl" }),
                /* @__PURE__ */ jsxRuntimeExports.jsx("th", { className: "col-right", children: "Next" }),
                /* @__PURE__ */ jsxRuntimeExports.jsx("th", { children: "Missing Resources" }),
                /* @__PURE__ */ jsxRuntimeExports.jsx("th", { className: "col-right", children: "Build Time" }),
                /* @__PURE__ */ jsxRuntimeExports.jsx("th", { className: "col-right", children: "Res Ready" }),
                /* @__PURE__ */ jsxRuntimeExports.jsx("th", { className: "col-right", children: "Level Reached" }),
                /* @__PURE__ */ jsxRuntimeExports.jsx("th", { className: "col-right", children: "Control" })
              ] }) }),
              /* @__PURE__ */ jsxRuntimeExports.jsx("tbody", { id: "eta-table-tbody", children: !etaData ? /* @__PURE__ */ jsxRuntimeExports.jsx("tr", { children: /* @__PURE__ */ jsxRuntimeExports.jsx("td", { colSpan: 8, className: "empty-cell", children: "Waiting for building catalog..." }) }) : visibleRows.length === 0 ? /* @__PURE__ */ jsxRuntimeExports.jsx("tr", { children: /* @__PURE__ */ jsxRuntimeExports.jsx("td", { colSpan: 8, className: "empty-cell", children: "No items in selected category." }) }) : visibleRows.map((row, idx) => {
                const rowKey = row.key != null ? String(row.key).trim() : "";
                const isSelected = Boolean(selectedRow && selectedRow.key === row.key);
                const curLvl = finiteNum(row.current_level, 0);
                const rowPct = finiteNum(row.economy_rate_pct, null);
                const clampedRowPct = rowPct !== null ? clampPct$1(Math.round(rowPct)) : null;
                const missTotal = finiteNum(row.missing_total, 0);
                const waitEtaText = row.wait_eta ?? "—";
                const bnTag = typeof row.bottleneck === "string" && row.bottleneck.trim() ? bottleneckTag(row.bottleneck) : "";
                const reachEtaText = row.reach_eta ?? "—";
                const rowWaitH = finiteNum(row.wait_hours, 0);
                const rowIsRes = rowKey.startsWith("r") || isResearchTab;
                const isTurret = Boolean(row.is_defense_turret);
                const canExec = isTurret ? false : Boolean(rowIsRes ? row.can_research_live : row.can_build_live);
                const unitId = rowIsRes ? row.research_id : row.building_id;
                const nextLvl = finiteNum(row.next_level ?? row.target_level, null);
                const safeName = String(row.name || "Unit").replace(/>/g, "→");
                return /* @__PURE__ */ jsxRuntimeExports.jsxs(
                  "tr",
                  {
                    className: `clickable-row${isSelected ? " row-selected" : ""}`,
                    onClick: () => {
                      if (rowKey) {
                        setUiPref("selectedUnitKey", rowKey);
                      }
                    },
                    children: [
                      /* @__PURE__ */ jsxRuntimeExports.jsx("td", { children: /* @__PURE__ */ jsxRuntimeExports.jsxs("div", { className: "action-title", children: [
                        row.name || "Unit",
                        rowKey ? /* @__PURE__ */ jsxRuntimeExports.jsx("span", { className: "action-sub tabular", children: ` (${rowKey})` }) : null,
                        row.queued_count > 0 || row.active ? /* @__PURE__ */ jsxRuntimeExports.jsx("span", { className: "rate-chip", children: row.active ? "ACTIVE" : `Q x${row.queued_count}` }) : null,
                        clampedRowPct !== null && curLvl > 0 ? /* @__PURE__ */ jsxRuntimeExports.jsx(
                          "span",
                          {
                            className: `rate-chip tabular${clampedRowPct < 100 ? " is-paused" : ""}`,
                            children: `${clampedRowPct}%`
                          }
                        ) : null
                      ] }) }),
                      /* @__PURE__ */ jsxRuntimeExports.jsx("td", { className: "col-right tabular", children: String(curLvl) }),
                      /* @__PURE__ */ jsxRuntimeExports.jsx("td", { className: "col-right tabular", children: isTurret ? "—" : `L${row.next_level ?? row.target_level ?? "—"}` }),
                      /* @__PURE__ */ jsxRuntimeExports.jsx("td", { className: "tabular", children: isTurret ? formatResourcesInline(row.cost) : missTotal === 0 ? /* @__PURE__ */ jsxRuntimeExports.jsx("span", { className: "status-word", children: "Affordable" }) : formatResourcesInline(row.missing) }),
                      /* @__PURE__ */ jsxRuntimeExports.jsx("td", { className: "col-right tabular", children: row.build_time_text ?? "—" }),
                      /* @__PURE__ */ jsxRuntimeExports.jsx("td", { className: "col-right tabular", children: bnTag ? `${waitEtaText} (${bnTag})` : waitEtaText }),
                      /* @__PURE__ */ jsxRuntimeExports.jsx("td", { className: "col-right tabular", children: /* @__PURE__ */ jsxRuntimeExports.jsx(
                        "span",
                        {
                          className: `eta-pill${reachEtaText === "—" ? " is-blocked" : rowWaitH > 0 ? " is-wait" : ""}`,
                          children: reachEtaText
                        }
                      ) }),
                      /* @__PURE__ */ jsxRuntimeExports.jsx("td", { className: "col-right", children: /* @__PURE__ */ jsxRuntimeExports.jsxs("div", { className: "row-actions-wrap", children: [
                        canExec ? /* @__PURE__ */ jsxRuntimeExports.jsx(
                          "button",
                          {
                            type: "button",
                            className: "btn btn-primary btn-sm",
                            onClick: (ev) => handleRowLiveBuild(ev, row, rowIsRes),
                            children: rowIsRes ? "Research" : "Build"
                          }
                        ) : Array.isArray(row.unmet_prerequisites) && row.unmet_prerequisites.length > 0 ? /* @__PURE__ */ jsxRuntimeExports.jsx("span", { className: "action-sub", children: "Locked" }) : null,
                        !isTurret && unitId != null && nextLvl !== null && nextLvl > 0 ? /* @__PURE__ */ jsxRuntimeExports.jsx(
                          "button",
                          {
                            type: "button",
                            className: "btn btn-secondary btn-sm",
                            title: `Add ${safeName} L${nextLvl} to P1 priority queue`,
                            "aria-label": `Add ${safeName} L${nextLvl} to P1 priority queue`,
                            onClick: (ev) => handleRowPinQueue(ev, row, rowIsRes),
                            children: "+ Queue"
                          }
                        ) : null,
                        !rowIsRes && !isTurret && row.building_id === 6 && curLvl > 0 ? /* @__PURE__ */ jsxRuntimeExports.jsx(
                          "button",
                          {
                            type: "button",
                            className: "btn btn-secondary btn-sm btn-danger-ghost",
                            title: "Demolish Thermosolar plant (b6)",
                            onClick: (ev) => handleRowDemolishB6(ev, row),
                            children: "Demolish"
                          }
                        ) : null
                      ] }) })
                    ]
                  },
                  rowKey || `unit-${idx}`
                );
              }) })
            ] }) }),
            /* @__PURE__ */ jsxRuntimeExports.jsx(BuildingInspector, { etaData, selectedRow, bfPct })
          ] }),
          /* @__PURE__ */ jsxRuntimeExports.jsxs("div", { className: "intel-pane", id: "bld-pane-universe-research", hidden: !isUniverseResearchTab, children: [
            /* @__PURE__ */ jsxRuntimeExports.jsxs("div", { className: "window-bar window-bar-wrap", children: [
              /* @__PURE__ */ jsxRuntimeExports.jsxs("div", { className: "window-title-group", children: [
                /* @__PURE__ */ jsxRuntimeExports.jsx("span", { className: "module-code tabular", children: "UNV" }),
                /* @__PURE__ */ jsxRuntimeExports.jsx("h3", { children: "Universe Max Research Levels" }),
                /* @__PURE__ */ jsxRuntimeExports.jsx("span", { className: "module-meta tabular", children: `${universeMaxRows.length} TECHNOLOGIES` })
              ] }),
              /* @__PURE__ */ jsxRuntimeExports.jsx("div", { className: "inline-actions", children: /* @__PURE__ */ jsxRuntimeExports.jsx(
                "button",
                {
                  type: "button",
                  className: "btn btn-secondary btn-sm",
                  id: "btn-bld-refresh-universe-research",
                  disabled: syncingUniverseMax,
                  onClick: handleRefreshUniverseResearch,
                  children: syncingUniverseMax ? "Syncing..." : "Refresh Max Research"
                }
              ) })
            ] }),
            /* @__PURE__ */ jsxRuntimeExports.jsx("div", { className: "table-wrap table-scroll", children: /* @__PURE__ */ jsxRuntimeExports.jsxs("table", { id: "bld-universe-research-table", children: [
              /* @__PURE__ */ jsxRuntimeExports.jsx("thead", { children: /* @__PURE__ */ jsxRuntimeExports.jsxs("tr", { children: [
                /* @__PURE__ */ jsxRuntimeExports.jsx("th", { children: "Research" }),
                /* @__PURE__ */ jsxRuntimeExports.jsx("th", { className: "col-right", children: "Your Level" }),
                /* @__PURE__ */ jsxRuntimeExports.jsx("th", { className: "col-right", children: "Universe Max" }),
                /* @__PURE__ */ jsxRuntimeExports.jsx("th", { className: "col-right", children: "Delta / Gap" }),
                /* @__PURE__ */ jsxRuntimeExports.jsx("th", { className: "col-right", children: "Spied Max" }),
                /* @__PURE__ */ jsxRuntimeExports.jsx("th", { children: "Record Holder" }),
                /* @__PURE__ */ jsxRuntimeExports.jsx("th", { className: "col-right", children: "Catalog Cap" }),
                /* @__PURE__ */ jsxRuntimeExports.jsx("th", { className: "col-right", children: "Updated" })
              ] }) }),
              /* @__PURE__ */ jsxRuntimeExports.jsx("tbody", { id: "bld-universe-research-tbody", children: universeMaxRows.length === 0 ? /* @__PURE__ */ jsxRuntimeExports.jsx("tr", { children: /* @__PURE__ */ jsxRuntimeExports.jsx("td", { colSpan: 8, className: "empty-cell", children: "Click Refresh Max Research to compute and persist universe research records." }) }) : universeMaxRows.map((r, idx) => {
                const ownLvl = Math.max(0, Math.round(finiteNum(r.own_level, 0)));
                const uniMax = Math.max(ownLvl, Math.round(finiteNum(r.universe_max_level, ownLvl)));
                const spiedMax = Math.max(0, Math.round(finiteNum(r.spied_max_level, 0)));
                const catCap = finiteNum(r.catalog_max_level, null);
                const gap = Math.max(0, finiteNum(r.gap_to_max, uniMax - ownLvl));
                const isLeader = uniMax > 0 && ownLvl >= uniMax;
                const holderStr = r.holder ? `${r.holder}${r.holder_coords ? ` [${r.holder_coords}]` : ""}` : isLeader ? "You" : "—";
                return /* @__PURE__ */ jsxRuntimeExports.jsxs("tr", { children: [
                  /* @__PURE__ */ jsxRuntimeExports.jsx("td", { children: /* @__PURE__ */ jsxRuntimeExports.jsxs("div", { className: "action-title", children: [
                    r.name || r.slug || "Research",
                    r.key ? /* @__PURE__ */ jsxRuntimeExports.jsx("span", { className: "action-sub tabular", children: ` (${r.key})` }) : null
                  ] }) }),
                  /* @__PURE__ */ jsxRuntimeExports.jsx("td", { className: "col-right tabular", children: `L${ownLvl}` }),
                  /* @__PURE__ */ jsxRuntimeExports.jsx("td", { className: "col-right tabular", children: /* @__PURE__ */ jsxRuntimeExports.jsx("span", { className: `eta-pill${isLeader ? "" : gap > 0 ? " is-wait" : ""}`, children: `L${uniMax}` }) }),
                  /* @__PURE__ */ jsxRuntimeExports.jsx("td", { className: "col-right tabular", children: isLeader ? "Leader (0)" : gap > 0 ? `-${gap} Lvl` : "0" }),
                  /* @__PURE__ */ jsxRuntimeExports.jsx("td", { className: "col-right tabular", children: `L${spiedMax}` }),
                  /* @__PURE__ */ jsxRuntimeExports.jsx("td", { children: holderStr }),
                  /* @__PURE__ */ jsxRuntimeExports.jsx("td", { className: "col-right tabular", children: catCap !== null && catCap > 0 ? `L${Math.round(catCap)}` : "—" }),
                  /* @__PURE__ */ jsxRuntimeExports.jsx("td", { className: "col-right tabular", children: fmtShortDateTime(r.updated_ts) })
                ] }, r.key || r.slug || idx);
              }) })
            ] }) })
          ] })
        ] })
      }
    );
  };
  const SIM_POPULATE_EVENT = "si:populate-combat-sim";
  function dispatchPopulateSimEvent(detail) {
    if (typeof window === "undefined") return;
    window.dispatchEvent(new CustomEvent(SIM_POPULATE_EVENT, { detail }));
  }
  const ATK_KEYS = ["s4", "s6", "s7", "s13", "s1", "s2"];
  const INITIAL_INPUTS = {
    atk: {
      s4: "10",
      s6: "0",
      s7: "0",
      s13: "0",
      s1: "4",
      s2: "0"
    },
    defD1: "2",
    defD2: "0",
    defD4: "0",
    defS4: "0",
    lootTotal: "20000",
    margin: "1.4"
  };
  const INITIAL_RESULT = {
    verdictText: "READY",
    isUnsafe: false,
    ratioText: "Configure forces and run 1:1 combat estimation.",
    atkPwr: "—",
    defPwr: "—",
    cargo: "—",
    plunder: "—",
    notes: "No rapidfire: units resolve 1:1 against attacker weapon/shield/defense tech levels."
  };
  function ownedAtkMapFrom(cmb) {
    const byKey = new Map(
      Array.isArray(cmb == null ? void 0 : cmb.ships) ? cmb.ships.map((s) => [String((s == null ? void 0 : s.key) || ""), Number((s == null ? void 0 : s.owned) || 0)]) : []
    );
    return {
      s4: String(byKey.get("s4") || 0),
      s6: String(byKey.get("s6") || 0),
      s7: String(byKey.get("s7") || 0),
      s13: String(byKey.get("s13") || 0),
      s1: String(byKey.get("s1") || 0),
      s2: String(byKey.get("s2") || 0)
    };
  }
  const CombatSimulator = ({ cmb }) => {
    const { apiCommand: apiCommand2, showFeedback, selectedPlanetId } = useConsole();
    const [inputs, setInputs] = reactExports.useState(INITIAL_INPUTS);
    const [result, setResult] = reactExports.useState(INITIAL_RESULT);
    const populatedPlanetRef = reactExports.useRef(void 0);
    const inputsRef = reactExports.useRef(inputs);
    inputsRef.current = inputs;
    const runSimulationWith = reactExports.useCallback(
      async (draft) => {
        const atk = {};
        for (const key of ATK_KEYS) {
          const val = Math.max(0, Math.round(finiteNum(draft.atk[key], 0)));
          if (val > 0) atk[key] = val;
        }
        const defShips = {};
        const s4 = Math.max(0, Math.round(finiteNum(draft.defS4, 0)));
        if (s4 > 0) defShips.s4 = s4;
        const defDefs = {};
        const d1 = Math.max(0, Math.round(finiteNum(draft.defD1, 0)));
        const d2 = Math.max(0, Math.round(finiteNum(draft.defD2, 0)));
        const d4 = Math.max(0, Math.round(finiteNum(draft.defD4, 0)));
        if (d1 > 0) defDefs.d1 = d1;
        if (d2 > 0) defDefs.d2 = d2;
        if (d4 > 0) defDefs.d4 = d4;
        const totalLoot = Math.max(0, Math.round(finiteNum(draft.lootTotal, 0)));
        const split = Math.floor(totalLoot / 4);
        const margin = Math.max(1, finiteNum(draft.margin, 1.4));
        try {
          const res = await apiCommand2({
            command: "simulate_combat",
            attacker_ships: atk,
            defender_ships: defShips,
            defender_defense: defDefs,
            target_resources: {
              pig_iron: split,
              metal: split,
              kryptonite: split,
              spice: totalLoot - split * 3
            },
            safety_margin: margin
          });
          const sim = res.simulation || {};
          const safe = Boolean(sim.safe_to_attack);
          const winRatioNum = finiteNum(sim.win_ratio, null);
          const ratioStr = sim.win_ratio === Infinity || sim.win_ratio === "Infinity" ? "INF" : winRatioNum !== null ? winRatioNum.toFixed(2) : "0.00";
          setResult({
            verdictText: safe ? "SAFE TO RAID" : "UNSAFE / HOLD",
            isUnsafe: !safe,
            ratioText: `Power Ratio: ${ratioStr}x (Threshold ${finiteNum(sim.safety_margin, margin)}x)`,
            atkPwr: fmtInt(Math.round(finiteNum(sim.attacker_power, 0))),
            defPwr: fmtInt(Math.round(finiteNum(sim.defender_power, 0))),
            cargo: fmtInt(finiteNum(sim.cargo_capacity, 0)),
            plunder: `${fmtInt(finiteNum(sim.total_plunder, 0))} (${fmtInt(Math.round(finiteNum(sim.loot_spice_equivalent, 0)))} SpEq)`,
            notes: String(sim.notes || "Simulation complete.")
          });
        } catch (err) {
          showFeedback(`Simulation failed: ${err.message}`, feedbackKindForError(err));
        }
      },
      [apiCommand2, showFeedback]
    );
    reactExports.useEffect(() => {
      if (populatedPlanetRef.current !== void 0 && populatedPlanetRef.current !== selectedPlanetId) {
        populatedPlanetRef.current = void 0;
      }
    }, [selectedPlanetId]);
    reactExports.useEffect(() => {
      const legacySt = globalThis == null ? void 0 : globalThis.state;
      const ships = Array.isArray(cmb == null ? void 0 : cmb.ships) ? cmb.ships : [];
      const hasOwned = ships.some((s) => Number(s == null ? void 0 : s.owned) > 0);
      if (hasOwned && populatedPlanetRef.current === void 0 && !(legacySt && legacySt.simPopulatedOnce)) {
        populatedPlanetRef.current = selectedPlanetId;
        if (legacySt) legacySt.simPopulatedOnce = true;
        setInputs((prev) => ({
          ...prev,
          atk: ownedAtkMapFrom(cmb)
        }));
      }
    }, [cmb, selectedPlanetId]);
    reactExports.useEffect(() => {
      if (typeof window === "undefined") return;
      const onPopulate = (ev) => {
        const detail = ev.detail || {};
        const def = detail.defense || {};
        const flt = detail.fleet || {};
        const nextDraft = {
          ...inputsRef.current,
          atk: ownedAtkMapFrom(cmb),
          defD1: String(Math.max(0, Math.round(finiteNum(def.d1, 0)))),
          defD2: String(Math.max(0, Math.round(finiteNum(def.d2, 0)))),
          defD4: String(Math.max(0, Math.round(finiteNum(def.d4, 0)))),
          defS4: String(Math.max(0, Math.round(finiteNum(flt.s4, 0)))),
          lootTotal: String(Math.max(0, Math.round(finiteNum(detail.totalLoot, 0))))
        };
        setInputs(nextDraft);
        runSimulationWith(nextDraft);
      };
      window.addEventListener(SIM_POPULATE_EVENT, onPopulate);
      return () => window.removeEventListener(SIM_POPULATE_EVENT, onPopulate);
    }, [cmb, runSimulationWith]);
    const handleUseOwned = () => {
      const nextDraft = {
        ...inputs,
        atk: ownedAtkMapFrom(cmb)
      };
      setInputs(nextDraft);
      runSimulationWith(nextDraft);
    };
    const setAtkField = (key, val) => {
      setInputs((prev) => ({
        ...prev,
        atk: { ...prev.atk, [key]: val }
      }));
    };
    return /* @__PURE__ */ jsxRuntimeExports.jsxs("article", { className: "hud-window", children: [
      /* @__PURE__ */ jsxRuntimeExports.jsxs("div", { className: "window-bar", children: [
        /* @__PURE__ */ jsxRuntimeExports.jsxs("div", { className: "window-title-group", children: [
          /* @__PURE__ */ jsxRuntimeExports.jsx("span", { className: "module-code tabular", children: "SIM" }),
          /* @__PURE__ */ jsxRuntimeExports.jsx("h2", { children: "1:1 Zero-Rapidfire Raid Simulator" })
        ] }),
        /* @__PURE__ */ jsxRuntimeExports.jsxs("div", { className: "inline-actions", children: [
          /* @__PURE__ */ jsxRuntimeExports.jsx(
            "button",
            {
              type: "button",
              className: "btn btn-secondary btn-sm",
              id: "btn-sim-use-owned",
              onClick: handleUseOwned,
              children: "Use Owned Fleet"
            }
          ),
          /* @__PURE__ */ jsxRuntimeExports.jsx(
            "button",
            {
              type: "button",
              className: "btn btn-primary btn-sm",
              id: "btn-run-sim",
              onClick: () => runSimulationWith(inputs),
              children: "Simulate Raid"
            }
          )
        ] })
      ] }),
      /* @__PURE__ */ jsxRuntimeExports.jsxs("div", { className: "sim-body", children: [
        /* @__PURE__ */ jsxRuntimeExports.jsxs("div", { className: "sim-inputs-grid", children: [
          /* @__PURE__ */ jsxRuntimeExports.jsxs("div", { className: "sim-group", children: [
            /* @__PURE__ */ jsxRuntimeExports.jsx("h3", { children: "Attacker Ships" }),
            /* @__PURE__ */ jsxRuntimeExports.jsxs("div", { className: "sim-fields", children: [
              /* @__PURE__ */ jsxRuntimeExports.jsxs("label", { children: [
                "Small Fighter (s4)",
                " ",
                /* @__PURE__ */ jsxRuntimeExports.jsx(
                  "input",
                  {
                    type: "number",
                    id: "sim-atk-s4",
                    min: 0,
                    value: inputs.atk.s4,
                    onChange: (e) => setAtkField("s4", e.target.value)
                  }
                )
              ] }),
              /* @__PURE__ */ jsxRuntimeExports.jsxs("label", { children: [
                "Starcruiser (s6)",
                " ",
                /* @__PURE__ */ jsxRuntimeExports.jsx(
                  "input",
                  {
                    type: "number",
                    id: "sim-atk-s6",
                    min: 0,
                    value: inputs.atk.s6,
                    onChange: (e) => setAtkField("s6", e.target.value)
                  }
                )
              ] }),
              /* @__PURE__ */ jsxRuntimeExports.jsxs("label", { children: [
                "Phoenix (s7)",
                " ",
                /* @__PURE__ */ jsxRuntimeExports.jsx(
                  "input",
                  {
                    type: "number",
                    id: "sim-atk-s7",
                    min: 0,
                    value: inputs.atk.s7,
                    onChange: (e) => setAtkField("s7", e.target.value)
                  }
                )
              ] }),
              /* @__PURE__ */ jsxRuntimeExports.jsxs("label", { children: [
                "Battleship (s13)",
                " ",
                /* @__PURE__ */ jsxRuntimeExports.jsx(
                  "input",
                  {
                    type: "number",
                    id: "sim-atk-s13",
                    min: 0,
                    value: inputs.atk.s13,
                    onChange: (e) => setAtkField("s13", e.target.value)
                  }
                )
              ] }),
              /* @__PURE__ */ jsxRuntimeExports.jsxs("label", { children: [
                "Small Transp. (s1)",
                " ",
                /* @__PURE__ */ jsxRuntimeExports.jsx(
                  "input",
                  {
                    type: "number",
                    id: "sim-atk-s1",
                    min: 0,
                    value: inputs.atk.s1,
                    onChange: (e) => setAtkField("s1", e.target.value)
                  }
                )
              ] }),
              /* @__PURE__ */ jsxRuntimeExports.jsxs("label", { children: [
                "Large Transp. (s2)",
                " ",
                /* @__PURE__ */ jsxRuntimeExports.jsx(
                  "input",
                  {
                    type: "number",
                    id: "sim-atk-s2",
                    min: 0,
                    value: inputs.atk.s2,
                    onChange: (e) => setAtkField("s2", e.target.value)
                  }
                )
              ] })
            ] })
          ] }),
          /* @__PURE__ */ jsxRuntimeExports.jsxs("div", { className: "sim-group", children: [
            /* @__PURE__ */ jsxRuntimeExports.jsx("h3", { children: "Defender Fleet & Turrets" }),
            /* @__PURE__ */ jsxRuntimeExports.jsxs("div", { className: "sim-fields", children: [
              /* @__PURE__ */ jsxRuntimeExports.jsxs("label", { children: [
                "Missile Turret (d1)",
                " ",
                /* @__PURE__ */ jsxRuntimeExports.jsx(
                  "input",
                  {
                    type: "number",
                    id: "sim-def-d1",
                    min: 0,
                    value: inputs.defD1,
                    onChange: (e) => setInputs((prev) => ({ ...prev, defD1: e.target.value }))
                  }
                )
              ] }),
              /* @__PURE__ */ jsxRuntimeExports.jsxs("label", { children: [
                "Laser Battery (d2)",
                " ",
                /* @__PURE__ */ jsxRuntimeExports.jsx(
                  "input",
                  {
                    type: "number",
                    id: "sim-def-d2",
                    min: 0,
                    value: inputs.defD2,
                    onChange: (e) => setInputs((prev) => ({ ...prev, defD2: e.target.value }))
                  }
                )
              ] }),
              /* @__PURE__ */ jsxRuntimeExports.jsxs("label", { children: [
                "Rocket Dome (d4)",
                " ",
                /* @__PURE__ */ jsxRuntimeExports.jsx(
                  "input",
                  {
                    type: "number",
                    id: "sim-def-d4",
                    min: 0,
                    value: inputs.defD4,
                    onChange: (e) => setInputs((prev) => ({ ...prev, defD4: e.target.value }))
                  }
                )
              ] }),
              /* @__PURE__ */ jsxRuntimeExports.jsxs("label", { children: [
                "Small Fighter (s4)",
                " ",
                /* @__PURE__ */ jsxRuntimeExports.jsx(
                  "input",
                  {
                    type: "number",
                    id: "sim-def-s4",
                    min: 0,
                    value: inputs.defS4,
                    onChange: (e) => setInputs((prev) => ({ ...prev, defS4: e.target.value }))
                  }
                )
              ] }),
              /* @__PURE__ */ jsxRuntimeExports.jsxs("label", { children: [
                "Target Total Loot",
                " ",
                /* @__PURE__ */ jsxRuntimeExports.jsx(
                  "input",
                  {
                    type: "number",
                    id: "sim-loot-total",
                    min: 0,
                    step: 1e3,
                    value: inputs.lootTotal,
                    onChange: (e) => setInputs((prev) => ({ ...prev, lootTotal: e.target.value }))
                  }
                )
              ] }),
              /* @__PURE__ */ jsxRuntimeExports.jsxs("label", { children: [
                "Safety Margin",
                " ",
                /* @__PURE__ */ jsxRuntimeExports.jsx(
                  "input",
                  {
                    type: "number",
                    id: "sim-margin",
                    min: 1,
                    max: 5,
                    step: 0.1,
                    value: inputs.margin,
                    onChange: (e) => setInputs((prev) => ({ ...prev, margin: e.target.value }))
                  }
                )
              ] })
            ] })
          ] })
        ] }),
        /* @__PURE__ */ jsxRuntimeExports.jsxs("div", { className: "sim-result-card", id: "sim-result-box", children: [
          /* @__PURE__ */ jsxRuntimeExports.jsxs("div", { className: "sim-verdict-row", id: "sim-verdict", children: [
            /* @__PURE__ */ jsxRuntimeExports.jsx(
              "span",
              {
                className: `run-badge tabular${result.isUnsafe ? " is-paused" : ""}`,
                id: "sim-verdict-badge",
                children: result.verdictText
              }
            ),
            /* @__PURE__ */ jsxRuntimeExports.jsx("span", { className: "tabular", id: "sim-ratio-text", children: result.ratioText })
          ] }),
          /* @__PURE__ */ jsxRuntimeExports.jsxs("div", { className: "sim-metrics-grid tabular", id: "sim-metrics-grid", children: [
            /* @__PURE__ */ jsxRuntimeExports.jsxs("div", { children: [
              /* @__PURE__ */ jsxRuntimeExports.jsx("span", { children: "Attacker Power" }),
              /* @__PURE__ */ jsxRuntimeExports.jsx("strong", { id: "sim-atk-pwr", children: result.atkPwr })
            ] }),
            /* @__PURE__ */ jsxRuntimeExports.jsxs("div", { children: [
              /* @__PURE__ */ jsxRuntimeExports.jsx("span", { children: "Defender Power" }),
              /* @__PURE__ */ jsxRuntimeExports.jsx("strong", { id: "sim-def-pwr", children: result.defPwr })
            ] }),
            /* @__PURE__ */ jsxRuntimeExports.jsxs("div", { children: [
              /* @__PURE__ */ jsxRuntimeExports.jsx("span", { children: "Cargo Capacity" }),
              /* @__PURE__ */ jsxRuntimeExports.jsx("strong", { id: "sim-cargo", children: result.cargo })
            ] }),
            /* @__PURE__ */ jsxRuntimeExports.jsxs("div", { children: [
              /* @__PURE__ */ jsxRuntimeExports.jsx("span", { children: "Est. 50% Plunder" }),
              /* @__PURE__ */ jsxRuntimeExports.jsx("strong", { id: "sim-plunder", children: result.plunder })
            ] })
          ] }),
          /* @__PURE__ */ jsxRuntimeExports.jsx("p", { className: "action-sub", id: "sim-notes", children: result.notes })
        ] })
      ] })
    ] });
  };
  const DEFAULT_OUTPUT = 'Click "Pack Escape Flight" to calculate optimal cargo distribution.';
  const EscapeFlightPane = ({ hidden = false, cmb }) => {
    const { activePlanet, apiCommand: apiCommand2 } = useConsole();
    const [targetCoords, setTargetCoords] = reactExports.useState("");
    const [customOutput, setCustomOutput] = reactExports.useState(null);
    const displayedOutput = customOutput !== null ? customOutput : (cmb == null ? void 0 : cmb.escape_plan) ? JSON.stringify(cmb.escape_plan, null, 2) : DEFAULT_OUTPUT;
    const handleCalcFleetsave = async () => {
      const trimmed = targetCoords.trim();
      try {
        const res = await apiCommand2({
          command: "fleetsave_plan",
          planet_id: activePlanet == null ? void 0 : activePlanet.planet_id,
          target_coords: trimmed || void 0
        });
        setCustomOutput(JSON.stringify(res.escape_plan, null, 2));
      } catch (err) {
        setCustomOutput(`Error: ${err.message}`);
      }
    };
    return /* @__PURE__ */ jsxRuntimeExports.jsxs("div", { className: "combat-subpane fleet-pane", id: "cmb-pane-escape", hidden, children: [
      /* @__PURE__ */ jsxRuntimeExports.jsx("p", { className: "muted", children: "Computes zero-scanner-visible Escape Flight resource packing from current colony reserves." }),
      /* @__PURE__ */ jsxRuntimeExports.jsxs("div", { className: "fleetsave-form", children: [
        /* @__PURE__ */ jsxRuntimeExports.jsxs("div", { className: "control-field", children: [
          /* @__PURE__ */ jsxRuntimeExports.jsx("label", { htmlFor: "fs-target", children: "Destination Coords (blank = next colony)" }),
          /* @__PURE__ */ jsxRuntimeExports.jsx(
            "input",
            {
              type: "text",
              id: "fs-target",
              placeholder: "e.g. 02:381:05",
              value: targetCoords,
              onChange: (e) => setTargetCoords(e.target.value)
            }
          )
        ] }),
        /* @__PURE__ */ jsxRuntimeExports.jsx(
          "button",
          {
            type: "button",
            className: "btn btn-primary btn-sm",
            id: "btn-calc-fleetsave",
            onClick: handleCalcFleetsave,
            children: "Pack Escape Flight"
          }
        )
      ] }),
      /* @__PURE__ */ jsxRuntimeExports.jsx("pre", { id: "fleetsave-output", className: "console-box", children: displayedOutput })
    ] });
  };
  const CombatView = ({ hidden }) => {
    var _a, _b, _c;
    const {
      dashboard,
      activePage,
      activePlanet,
      combatData,
      refreshCombatForSelectedPlanet,
      enqueueManualCommand,
      showFeedback,
      apiCommand: apiCommand2,
      loadDashboard,
      uiPrefs,
      setUiPref
    } = useConsole();
    const [shipQtys, setShipQtys] = reactExports.useState({});
    const isHidden = hidden ?? activePage !== "combat";
    const subTab = uiPrefs.combatSubTab || "ships";
    const cmb = reactExports.useMemo(() => {
      if (combatData && activePlanet && combatData.planet_id === activePlanet.planet_id) {
        return combatData;
      }
      return (dashboard == null ? void 0 : dashboard.combat) || combatData || null;
    }, [combatData, activePlanet, dashboard == null ? void 0 : dashboard.combat]);
    const az = (cmb == null ? void 0 : cmb.attack_zone) || {};
    const techs = (cmb == null ? void 0 : cmb.combat_techs) || {};
    const fltPow = finiteNum(cmb == null ? void 0 : cmb.stationed_fleet_power, 0);
    const defPow = finiteNum(cmb == null ? void 0 : cmb.stationed_defense_power, 0);
    const cargoCap = finiteNum(cmb == null ? void 0 : cmb.total_cargo_capacity, 0);
    const ships = Array.isArray(cmb == null ? void 0 : cmb.ships) ? cmb.ships : [];
    const defenses = Array.isArray(cmb == null ? void 0 : cmb.defenses) ? cmb.defenses : [];
    const offersById = reactExports.useMemo(
      () => new Map(
        Array.isArray(cmb == null ? void 0 : cmb.ship_offers) ? cmb.ship_offers.map((o) => [Number(o.unit_id), o]) : []
      ),
      [cmb == null ? void 0 : cmb.ship_offers]
    );
    const raidTargets = reactExports.useMemo(() => {
      var _a2, _b2, _c2;
      const ownCoords = new Set(
        (((_a2 = dashboard == null ? void 0 : dashboard.state) == null ? void 0 : _a2.planets) || []).map((p) => String(p.coords || "")).filter(Boolean)
      );
      const rawTargets = ((_b2 = dashboard == null ? void 0 : dashboard.db_summary) == null ? void 0 : _b2.spy_targets) || ((_c2 = dashboard == null ? void 0 : dashboard.state) == null ? void 0 : _c2.raid_targets) || [];
      return rawTargets.filter((t) => {
        const coords = String((t == null ? void 0 : t.coords) || (t == null ? void 0 : t.target_coords) || "");
        return Boolean(coords) && !ownCoords.has(coords) && (t == null ? void 0 : t.status_color) !== "free";
      });
    }, [(_a = dashboard == null ? void 0 : dashboard.db_summary) == null ? void 0 : _a.spy_targets, (_b = dashboard == null ? void 0 : dashboard.state) == null ? void 0 : _b.planets, (_c = dashboard == null ? void 0 : dashboard.state) == null ? void 0 : _c.raid_targets]);
    const handleNavJump = async (action) => {
      try {
        await apiCommand2({ command: "navigate", action });
        showFeedback(`Chrome switched to ${action}.`, "ok");
      } catch (err) {
        showFeedback(`Navigation failed: ${err.message}`, feedbackKindForError(err));
      }
    };
    const handleScanGalaxy = async () => {
      try {
        showFeedback("Scanning home Galaxy system in Chrome...", "ok");
        const res = await apiCommand2({
          command: "scan_galaxy",
          planet_id: activePlanet == null ? void 0 : activePlanet.planet_id,
          dry_run: false
        });
        setUiPref("combatSubTab", "targets");
        showFeedback(res.message || "Galaxy scan completed.", "ok");
        await loadDashboard({ refresh: false, force: true });
      } catch (err) {
        showFeedback(`Galaxy scan failed: ${err.message}`, feedbackKindForError(err));
      }
    };
    const handleRefreshCombat = async () => {
      showFeedback("Refreshing live Shipyard offers from Chrome...", "ok");
      const ok = await refreshCombatForSelectedPlanet({ refresh: true });
      if (ok) {
        showFeedback("Shipyard & Combat state refreshed.", "ok");
      }
    };
    const handleBuildShip = async (ship) => {
      const unitId = Number(ship.unit_id);
      const qty = Math.max(1, Math.round(finiteNum(shipQtys[unitId], 1)));
      try {
        const res = await apiCommand2({
          command: "build_ship",
          ship_id: unitId,
          quantity: qty,
          planet_id: (cmb == null ? void 0 : cmb.planet_id) ?? (activePlanet == null ? void 0 : activePlanet.planet_id),
          dry_run: false
        });
        showFeedback(res.message || `Ordered ${qty}x ${ship.name}.`, "ok");
        await loadDashboard({ refresh: false, force: true });
      } catch (err) {
        showFeedback(`Ship order failed: ${err.message}`, feedbackKindForError(err));
      }
    };
    const handleQueueShip = (ship) => {
      const unitId = Number(ship.unit_id);
      const qty = Math.max(1, Math.round(finiteNum(shipQtys[unitId], 1)));
      enqueueManualCommand({
        kind: "BUILD_SHIP",
        priority: "P1",
        position: "top",
        planet_id: (cmb == null ? void 0 : cmb.planet_id) || (activePlanet == null ? void 0 : activePlanet.planet_id) || 0,
        target_id: unitId,
        target_name: String(ship.name || ""),
        target_level: qty
      });
    };
    const handleAttackTarget = async (coords) => {
      try {
        showFeedback(`Sending attack to ${coords}...`, "ok");
        const res = await apiCommand2({
          command: "attack",
          target_coords: coords,
          planet_id: (cmb == null ? void 0 : cmb.planet_id) ?? (activePlanet == null ? void 0 : activePlanet.planet_id),
          dry_run: false
        });
        showFeedback(
          res.message || `Attack processed for ${coords}.`,
          res.executed ? "ok" : "warn"
        );
        await loadDashboard({ refresh: false, force: true });
      } catch (err) {
        showFeedback(`Attack failed: ${err.message}`, feedbackKindForError(err));
      }
    };
    return /* @__PURE__ */ jsxRuntimeExports.jsxs(
      "section",
      {
        id: "view-combat",
        className: "page-view",
        "aria-label": "Combat simulator and fleet overview",
        hidden: isHidden,
        children: [
          /* @__PURE__ */ jsxRuntimeExports.jsxs("div", { className: "combat-top-cards", children: [
            /* @__PURE__ */ jsxRuntimeExports.jsxs("div", { className: "kpi-card", children: [
              /* @__PURE__ */ jsxRuntimeExports.jsx("span", { className: "kpi-label tabular", children: "Attack Zone & Bash" }),
              /* @__PURE__ */ jsxRuntimeExports.jsx("strong", { className: "kpi-value tabular", id: "cmb-az-val", children: cmb ? `${az.label || "AZ —"} (${cmb.newbie_protected ? "Newbie Shield" : "Open"})` : "AZ —" }),
              /* @__PURE__ */ jsxRuntimeExports.jsx("span", { className: "kpi-sub tabular", id: "cmb-az-rule", children: cmb ? `${az.range_text || "—"} pts · Margin ${cmb.safety_margin || 1.4}x · Bash 3/24h` : "±2 AZ · Max 3/24h active" })
            ] }),
            /* @__PURE__ */ jsxRuntimeExports.jsxs("div", { className: "kpi-card", children: [
              /* @__PURE__ */ jsxRuntimeExports.jsx("span", { className: "kpi-label tabular", children: "Combat Techs (r8 / r9 / r10)" }),
              /* @__PURE__ */ jsxRuntimeExports.jsx("strong", { className: "kpi-value tabular", id: "cmb-tech-val", children: `L${techs.weapons ?? 0} / L${techs.shield ?? 0} / L${techs.defense ?? 0}` }),
              /* @__PURE__ */ jsxRuntimeExports.jsx("span", { className: "kpi-sub tabular", id: "cmb-tech-sub", children: cmb ? `Spy L${techs.spy ?? 0} · Targeting L${techs.targeting ?? 0} · Scanner L${cmb.galaxy_scanner_level ?? 0}` : "Spy L0 · Targeting L0" })
            ] }),
            /* @__PURE__ */ jsxRuntimeExports.jsxs("div", { className: "kpi-card", children: [
              /* @__PURE__ */ jsxRuntimeExports.jsx("span", { className: "kpi-label tabular", children: "Stationed Power (Colony)" }),
              /* @__PURE__ */ jsxRuntimeExports.jsx("strong", { className: "kpi-value tabular", id: "cmb-power-val", children: `${fmtInt(Math.round(fltPow))} / ${fmtInt(Math.round(defPow))}` }),
              /* @__PURE__ */ jsxRuntimeExports.jsx("span", { className: "kpi-sub tabular", id: "cmb-cargo-val", children: `Cargo ${fmtInt(cargoCap)} · Weapons Fac L${(cmb == null ? void 0 : cmb.weapons_factory_level) ?? 0}` })
            ] }),
            /* @__PURE__ */ jsxRuntimeExports.jsxs("div", { className: "kpi-card", children: [
              /* @__PURE__ */ jsxRuntimeExports.jsx("span", { className: "kpi-label tabular", children: "Combat Navigation" }),
              /* @__PURE__ */ jsxRuntimeExports.jsxs("div", { className: "combat-jumps", children: [
                /* @__PURE__ */ jsxRuntimeExports.jsx(
                  "button",
                  {
                    type: "button",
                    className: "btn btn-secondary btn-sm nav-jump",
                    "data-action": "internalWeaponFactory",
                    onClick: () => handleNavJump("internalWeaponFactory"),
                    children: "Shipyard"
                  }
                ),
                /* @__PURE__ */ jsxRuntimeExports.jsx(
                  "button",
                  {
                    type: "button",
                    className: "btn btn-secondary btn-sm nav-jump",
                    "data-action": "internalFleet",
                    onClick: () => handleNavJump("internalFleet"),
                    children: "Fleet"
                  }
                ),
                /* @__PURE__ */ jsxRuntimeExports.jsx(
                  "button",
                  {
                    type: "button",
                    className: "btn btn-secondary btn-sm nav-jump",
                    "data-action": "internalGalaxy",
                    onClick: () => handleNavJump("internalGalaxy"),
                    children: "Galaxy"
                  }
                ),
                /* @__PURE__ */ jsxRuntimeExports.jsx(
                  "button",
                  {
                    type: "button",
                    className: "btn btn-primary btn-sm",
                    id: "btn-scan-galaxy",
                    onClick: handleScanGalaxy,
                    children: "Scan Galaxy"
                  }
                ),
                /* @__PURE__ */ jsxRuntimeExports.jsx(
                  "button",
                  {
                    type: "button",
                    className: "btn btn-secondary btn-sm",
                    id: "btn-refresh-combat",
                    onClick: handleRefreshCombat,
                    children: "Sync Shipyard"
                  }
                )
              ] })
            ] })
          ] }),
          /* @__PURE__ */ jsxRuntimeExports.jsxs("div", { className: "combat-main-grid", children: [
            /* @__PURE__ */ jsxRuntimeExports.jsxs("article", { className: "hud-window", children: [
              /* @__PURE__ */ jsxRuntimeExports.jsxs("div", { className: "window-bar", children: [
                /* @__PURE__ */ jsxRuntimeExports.jsxs("div", { className: "window-title-group", children: [
                  /* @__PURE__ */ jsxRuntimeExports.jsx("span", { className: "module-code tabular", children: "FLT" }),
                  /* @__PURE__ */ jsxRuntimeExports.jsx("h2", { children: "Stationed Fleet, Defenses & Escape Flight" })
                ] }),
                /* @__PURE__ */ jsxRuntimeExports.jsxs("div", { className: "tab-group", role: "tablist", "aria-label": "Fleet subtabs", children: [
                  /* @__PURE__ */ jsxRuntimeExports.jsx(
                    "button",
                    {
                      type: "button",
                      className: `tab-btn${subTab === "ships" ? " active" : ""}`,
                      "data-cmb-tab": "ships",
                      onClick: () => setUiPref("combatSubTab", "ships"),
                      children: "Ships & Build"
                    }
                  ),
                  /* @__PURE__ */ jsxRuntimeExports.jsx(
                    "button",
                    {
                      type: "button",
                      className: `tab-btn${subTab === "defense" ? " active" : ""}`,
                      "data-cmb-tab": "defense",
                      onClick: () => setUiPref("combatSubTab", "defense"),
                      children: "Defense"
                    }
                  ),
                  /* @__PURE__ */ jsxRuntimeExports.jsx(
                    "button",
                    {
                      type: "button",
                      className: `tab-btn${subTab === "targets" ? " active" : ""}`,
                      id: "cmb-targets-tab",
                      "data-cmb-tab": "targets",
                      onClick: () => setUiPref("combatSubTab", "targets"),
                      children: `Targets (${raidTargets.length})`
                    }
                  ),
                  /* @__PURE__ */ jsxRuntimeExports.jsx(
                    "button",
                    {
                      type: "button",
                      className: `tab-btn${subTab === "escape" ? " active" : ""}`,
                      "data-cmb-tab": "escape",
                      onClick: () => setUiPref("combatSubTab", "escape"),
                      children: "Escape Flight"
                    }
                  )
                ] })
              ] }),
              /* @__PURE__ */ jsxRuntimeExports.jsx("div", { className: "combat-subpane", id: "cmb-pane-ships", hidden: subTab !== "ships", children: /* @__PURE__ */ jsxRuntimeExports.jsx("div", { className: "table-wrap table-scroll", children: /* @__PURE__ */ jsxRuntimeExports.jsxs("table", { children: [
                /* @__PURE__ */ jsxRuntimeExports.jsx("thead", { children: /* @__PURE__ */ jsxRuntimeExports.jsxs("tr", { children: [
                  /* @__PURE__ */ jsxRuntimeExports.jsx("th", { children: "Ship Class" }),
                  /* @__PURE__ */ jsxRuntimeExports.jsx("th", { className: "col-right", children: "Owned" }),
                  /* @__PURE__ */ jsxRuntimeExports.jsx("th", { className: "col-right", children: "ATK / SHD" }),
                  /* @__PURE__ */ jsxRuntimeExports.jsx("th", { className: "col-right", children: "Cargo" }),
                  /* @__PURE__ */ jsxRuntimeExports.jsx("th", { className: "col-right", children: "Order" })
                ] }) }),
                /* @__PURE__ */ jsxRuntimeExports.jsx("tbody", { id: "cmb-ships-tbody", "data-testid": "combat-shipyard-tbody", children: ships.length === 0 ? /* @__PURE__ */ jsxRuntimeExports.jsx("tr", { children: /* @__PURE__ */ jsxRuntimeExports.jsx("td", { colSpan: 5, className: "empty-cell", children: "No shipyard telemetry yet." }) }) : ships.map((s) => {
                  const uid = Number(s.unit_id);
                  const offer = offersById.get(uid);
                  return /* @__PURE__ */ jsxRuntimeExports.jsxs("tr", { children: [
                    /* @__PURE__ */ jsxRuntimeExports.jsxs("td", { children: [
                      `${s.name} `,
                      /* @__PURE__ */ jsxRuntimeExports.jsx("span", { className: "action-sub tabular", children: `(${s.key})` })
                    ] }),
                    /* @__PURE__ */ jsxRuntimeExports.jsx("td", { className: "col-right tabular", children: fmtInt(s.owned) }),
                    /* @__PURE__ */ jsxRuntimeExports.jsx("td", { className: "col-right tabular", children: `${fmtInt(s.attack)} / ${fmtInt(s.shield)}` }),
                    /* @__PURE__ */ jsxRuntimeExports.jsx("td", { className: "col-right tabular", children: fmtInt(s.cargo) }),
                    /* @__PURE__ */ jsxRuntimeExports.jsx("td", { className: "col-right", children: /* @__PURE__ */ jsxRuntimeExports.jsxs("div", { className: "row-actions-wrap", children: [
                      /* @__PURE__ */ jsxRuntimeExports.jsx(
                        "input",
                        {
                          type: "number",
                          min: 1,
                          value: shipQtys[uid] ?? "1",
                          onChange: (e) => setShipQtys((prev) => ({ ...prev, [uid]: e.target.value })),
                          className: "qty-inline",
                          "aria-label": `Order quantity for ${s.name}`
                        }
                      ),
                      offer && offer.can_build && /* @__PURE__ */ jsxRuntimeExports.jsx(
                        "button",
                        {
                          type: "button",
                          className: "btn btn-primary btn-sm",
                          onClick: () => handleBuildShip(s),
                          children: "Build"
                        }
                      ),
                      /* @__PURE__ */ jsxRuntimeExports.jsx(
                        "button",
                        {
                          type: "button",
                          className: "btn btn-secondary btn-sm",
                          title: `Add ${s.name} order to P1 priority queue`,
                          "aria-label": `Add ${s.name} order to P1 priority queue`,
                          onClick: () => handleQueueShip(s),
                          children: "+ Queue"
                        }
                      )
                    ] }) })
                  ] }, s.key || uid);
                }) })
              ] }) }) }),
              /* @__PURE__ */ jsxRuntimeExports.jsx("div", { className: "combat-subpane", id: "cmb-pane-defense", hidden: subTab !== "defense", children: /* @__PURE__ */ jsxRuntimeExports.jsx("div", { className: "table-wrap table-scroll", children: /* @__PURE__ */ jsxRuntimeExports.jsxs("table", { children: [
                /* @__PURE__ */ jsxRuntimeExports.jsx("thead", { children: /* @__PURE__ */ jsxRuntimeExports.jsxs("tr", { children: [
                  /* @__PURE__ */ jsxRuntimeExports.jsx("th", { children: "Defense Structure" }),
                  /* @__PURE__ */ jsxRuntimeExports.jsx("th", { className: "col-right", children: "Owned" }),
                  /* @__PURE__ */ jsxRuntimeExports.jsx("th", { className: "col-right", children: "ATK / SHD" }),
                  /* @__PURE__ */ jsxRuntimeExports.jsx("th", { className: "col-right", children: "Hull" })
                ] }) }),
                /* @__PURE__ */ jsxRuntimeExports.jsx("tbody", { id: "cmb-def-tbody", children: defenses.length === 0 ? /* @__PURE__ */ jsxRuntimeExports.jsx("tr", { children: /* @__PURE__ */ jsxRuntimeExports.jsx("td", { colSpan: 4, className: "empty-cell", children: "No defense data." }) }) : defenses.map((d) => /* @__PURE__ */ jsxRuntimeExports.jsxs("tr", { children: [
                  /* @__PURE__ */ jsxRuntimeExports.jsx("td", { children: `${d.name} (${d.key})` }),
                  /* @__PURE__ */ jsxRuntimeExports.jsx("td", { className: "col-right tabular", children: fmtInt(d.owned) }),
                  /* @__PURE__ */ jsxRuntimeExports.jsx("td", { className: "col-right tabular", children: `${fmtInt(d.attack)} / ${fmtInt(d.shield)}` }),
                  /* @__PURE__ */ jsxRuntimeExports.jsx("td", { className: "col-right tabular", children: fmtInt(d.hull) })
                ] }, d.key || d.name)) })
              ] }) }) }),
              /* @__PURE__ */ jsxRuntimeExports.jsx("div", { className: "combat-subpane", id: "cmb-pane-targets", hidden: subTab !== "targets", children: /* @__PURE__ */ jsxRuntimeExports.jsx("div", { className: "table-wrap table-scroll", children: /* @__PURE__ */ jsxRuntimeExports.jsxs("table", { children: [
                /* @__PURE__ */ jsxRuntimeExports.jsx("thead", { children: /* @__PURE__ */ jsxRuntimeExports.jsxs("tr", { children: [
                  /* @__PURE__ */ jsxRuntimeExports.jsx("th", { children: "Coords" }),
                  /* @__PURE__ */ jsxRuntimeExports.jsx("th", { children: "Planet / Owner" }),
                  /* @__PURE__ */ jsxRuntimeExports.jsx("th", { children: "Type" }),
                  /* @__PURE__ */ jsxRuntimeExports.jsx("th", { className: "col-right", children: "Loot (SpEq)" }),
                  /* @__PURE__ */ jsxRuntimeExports.jsx("th", { className: "col-right", children: "Ops" })
                ] }) }),
                /* @__PURE__ */ jsxRuntimeExports.jsx("tbody", { id: "cmb-targets-tbody", children: raidTargets.length === 0 ? /* @__PURE__ */ jsxRuntimeExports.jsx("tr", { children: /* @__PURE__ */ jsxRuntimeExports.jsx("td", { colSpan: 5, className: "empty-cell", children: 'Click "Scan Galaxy" to populate yellow inactive & Spacia targets.' }) }) : raidTargets.map((t) => {
                  const coords = String(t.coords || t.target_coords || "—");
                  const resMap = t.resources || t;
                  const color = String(t.status_color || "yellow");
                  const totalRes = finiteNum(resMap.pig_iron, 0) + finiteNum(resMap.metal, 0) + finiteNum(resMap.kryptonite, 0) + finiteNum(resMap.spice, 0);
                  return /* @__PURE__ */ jsxRuntimeExports.jsxs("tr", { children: [
                    /* @__PURE__ */ jsxRuntimeExports.jsx("td", { className: "tabular", children: coords }),
                    /* @__PURE__ */ jsxRuntimeExports.jsx("td", { children: [t.planet_name, t.owner].filter(Boolean).join(" · ") || "Unknown" }),
                    /* @__PURE__ */ jsxRuntimeExports.jsx("td", { children: /* @__PURE__ */ jsxRuntimeExports.jsx(
                      "span",
                      {
                        className: `status-word${color === "yellow" || t.is_spacia ? "" : " is-warn"}`,
                        children: t.is_spacia ? "SPACIA" : color === "yellow" ? "INACTIVE" : color.toUpperCase()
                      }
                    ) }),
                    /* @__PURE__ */ jsxRuntimeExports.jsx("td", { className: "col-right tabular", children: Number.isFinite(totalRes) ? totalRes > 0 ? fmtInt(totalRes) : "Unspied" : "—" }),
                    /* @__PURE__ */ jsxRuntimeExports.jsx("td", { className: "col-right", children: /* @__PURE__ */ jsxRuntimeExports.jsxs("div", { className: "row-actions", children: [
                      /* @__PURE__ */ jsxRuntimeExports.jsx(
                        "button",
                        {
                          type: "button",
                          className: "btn btn-secondary btn-sm",
                          onClick: () => dispatchPopulateSimEvent({
                            defense: t.defense || {},
                            fleet: t.fleet || {},
                            totalLoot: totalRes
                          }),
                          children: "Sim"
                        }
                      ),
                      /* @__PURE__ */ jsxRuntimeExports.jsx(
                        "button",
                        {
                          type: "button",
                          className: "btn btn-primary btn-sm",
                          onClick: () => handleAttackTarget(coords),
                          children: "Attack"
                        }
                      )
                    ] }) })
                  ] }, coords);
                }) })
              ] }) }) }),
              /* @__PURE__ */ jsxRuntimeExports.jsx(EscapeFlightPane, { hidden: subTab !== "escape", cmb })
            ] }),
            /* @__PURE__ */ jsxRuntimeExports.jsx(CombatSimulator, { cmb })
          ] })
        ]
      }
    );
  };
  const EspionageView = ({ hidden }) => {
    const {
      dashboard,
      activePage,
      setActivePage,
      activePlanet,
      espData,
      refreshEspionageView,
      syncAllInboxFolders,
      showFeedback,
      apiCommand: apiCommand2,
      loadDashboard,
      uiPrefs,
      setUiPref
    } = useConsole();
    const isHidden = hidden ?? activePage !== "espionage";
    const espTab = uiPrefs.espTab === "reports" ? "reports" : "targets";
    const sum = (espData == null ? void 0 : espData.summary) || {};
    const targets = Array.isArray(espData == null ? void 0 : espData.targets) ? espData.targets : [];
    const reports = Array.isArray(espData == null ? void 0 : espData.reports) ? espData.reports : [];
    const bestSpEq = targets.reduce(
      (acc, t) => Math.max(acc, finiteNum(t == null ? void 0 : t.spice_eq, 0)),
      0
    );
    const galSweep = (dashboard == null ? void 0 : dashboard.galaxy_sweep) || null;
    const galTotal = Math.max(0, Math.round(finiteNum(galSweep == null ? void 0 : galSweep.total_systems, 0)));
    const galExplored = Math.max(0, Math.round(finiteNum(galSweep == null ? void 0 : galSweep.explored_systems, 0)));
    const galPct = galSweep && galSweep.coverage_pct != null ? Math.max(0, Math.min(100, finiteNum(galSweep.coverage_pct, 0))) : galTotal > 0 ? Math.max(0, Math.min(100, galExplored / galTotal * 100)) : null;
    const galPhaseText = String((galSweep == null ? void 0 : galSweep.scan_phase) || "").toLowerCase() === "targeted_refresh" ? "Targeted Refresh" : "Exploratory";
    const handleSpyTarget = async (coords) => {
      try {
        showFeedback(`Dispatching spy probe to ${coords}...`, "ok");
        const res = await apiCommand2({
          command: "spy",
          target_coords: coords,
          quantity: 1,
          planet_id: activePlanet == null ? void 0 : activePlanet.planet_id,
          dry_run: false
        });
        showFeedback(
          res.message || `Spy probe sent to ${coords}.`,
          res.executed ? "ok" : "warn"
        );
        await refreshEspionageView();
      } catch (err) {
        showFeedback(`Spy failed: ${err.message}`, feedbackKindForError(err));
      }
    };
    const handleSimTarget = (target) => {
      const resMap = target.resources || {};
      const totalLoot = finiteNum(resMap.pig_iron, 0) + finiteNum(resMap.metal, 0) + finiteNum(resMap.kryptonite, 0) + finiteNum(resMap.spice, 0);
      setActivePage("combat");
      dispatchPopulateSimEvent({
        defense: target.defense || {},
        fleet: target.fleet || {},
        totalLoot
      });
    };
    const handleAttackTarget = async (coords) => {
      try {
        showFeedback(`Dispatching raid to ${coords}...`, "ok");
        const res = await apiCommand2({
          command: "attack",
          target_coords: coords,
          planet_id: activePlanet == null ? void 0 : activePlanet.planet_id,
          dry_run: false
        });
        showFeedback(
          res.message || `Attack processed for ${coords}.`,
          res.executed ? "ok" : "warn"
        );
        await loadDashboard({ refresh: false, force: true });
        await refreshEspionageView();
      } catch (err) {
        showFeedback(`Attack failed: ${err.message}`, feedbackKindForError(err));
      }
    };
    const handleNavigateMessages = async () => {
      try {
        await apiCommand2({ command: "navigate", action: "internalMessages" });
        showFeedback("Chrome switched to internalMessages.", "ok");
      } catch (err) {
        showFeedback(`Navigation failed: ${err.message}`, feedbackKindForError(err));
      }
    };
    return /* @__PURE__ */ jsxRuntimeExports.jsxs(
      "section",
      {
        id: "view-espionage",
        className: "page-view",
        "aria-label": "Espionage targets and spy report archive",
        hidden: isHidden,
        children: [
          /* @__PURE__ */ jsxRuntimeExports.jsxs("div", { className: "combat-top-cards", children: [
            /* @__PURE__ */ jsxRuntimeExports.jsxs("div", { className: "kpi-card", children: [
              /* @__PURE__ */ jsxRuntimeExports.jsx("span", { className: "kpi-label tabular", children: "Spied Targets" }),
              /* @__PURE__ */ jsxRuntimeExports.jsx("strong", { className: "kpi-value tabular", id: "esp-kpi-targets", children: espData ? `${fmtInt(sum.spied_targets ?? 0)} / ${fmtInt(sum.total_targets ?? targets.length)}` : "0" }),
              /* @__PURE__ */ jsxRuntimeExports.jsx("span", { className: "kpi-sub tabular", id: "esp-kpi-reports", children: `${fmtInt(sum.total_reports ?? reports.length)} archived spy reports` })
            ] }),
            /* @__PURE__ */ jsxRuntimeExports.jsxs("div", { className: "kpi-card", children: [
              /* @__PURE__ */ jsxRuntimeExports.jsx("span", { className: "kpi-label tabular", children: "Spacia & Inactives" }),
              /* @__PURE__ */ jsxRuntimeExports.jsx("strong", { className: "kpi-value tabular", id: "esp-kpi-spacia", children: `${fmtInt(sum.spacia_count ?? 0)} Spacia` }),
              /* @__PURE__ */ jsxRuntimeExports.jsx("span", { className: "kpi-sub tabular", id: "esp-kpi-inactives", children: `${fmtInt(sum.inactive_count ?? 0)} yellow inactive planets` })
            ] }),
            /* @__PURE__ */ jsxRuntimeExports.jsxs("div", { className: "kpi-card", children: [
              /* @__PURE__ */ jsxRuntimeExports.jsx("span", { className: "kpi-label tabular", children: "Debris & Top Loot" }),
              /* @__PURE__ */ jsxRuntimeExports.jsx("strong", { className: "kpi-value tabular", id: "esp-kpi-debris", children: `${fmtInt(sum.debris_count ?? 0)} Debris` }),
              /* @__PURE__ */ jsxRuntimeExports.jsx("span", { className: "kpi-sub tabular", id: "esp-kpi-top-loot", children: espData ? `Peak target: ${fmtInt(Math.round(bestSpEq))} SpEq` : "Best loot: 0 SpEq" })
            ] }),
            /* @__PURE__ */ jsxRuntimeExports.jsxs("div", { className: "kpi-card", children: [
              /* @__PURE__ */ jsxRuntimeExports.jsx("span", { className: "kpi-label tabular", children: "Espionage Sync" }),
              /* @__PURE__ */ jsxRuntimeExports.jsxs("div", { className: "combat-jumps", children: [
                /* @__PURE__ */ jsxRuntimeExports.jsx(
                  "button",
                  {
                    type: "button",
                    className: "btn btn-primary btn-sm",
                    id: "btn-esp-sync",
                    "data-testid": "btn-sync-espionage",
                    onClick: syncAllInboxFolders,
                    children: "Sync All Folders"
                  }
                ),
                /* @__PURE__ */ jsxRuntimeExports.jsx(
                  "button",
                  {
                    type: "button",
                    className: "btn btn-secondary btn-sm",
                    id: "btn-esp-refresh",
                    onClick: refreshEspionageView,
                    children: "Refresh DB"
                  }
                ),
                /* @__PURE__ */ jsxRuntimeExports.jsx(
                  "button",
                  {
                    type: "button",
                    className: "btn btn-secondary btn-sm nav-jump",
                    "data-action": "internalMessages",
                    onClick: handleNavigateMessages,
                    children: "Messages"
                  }
                )
              ] })
            ] })
          ] }),
          /* @__PURE__ */ jsxRuntimeExports.jsxs("article", { className: "hud-window page-fill", children: [
            /* @__PURE__ */ jsxRuntimeExports.jsxs("div", { className: "window-bar window-bar-wrap", children: [
              /* @__PURE__ */ jsxRuntimeExports.jsxs("div", { className: "window-title-group", children: [
                /* @__PURE__ */ jsxRuntimeExports.jsx("span", { className: "module-code tabular", children: "SPY" }),
                /* @__PURE__ */ jsxRuntimeExports.jsx("h2", { children: "Espionage Intelligence & Spy Report Archive" }),
                /* @__PURE__ */ jsxRuntimeExports.jsx("span", { className: "module-meta tabular", id: "esp-meta-count", children: espData ? `${targets.length} TARGETS · ${reports.length} REPORTS` : "0 TARGETS" }),
                /* @__PURE__ */ jsxRuntimeExports.jsx(
                  "span",
                  {
                    className: "gal-cov-inline tabular",
                    id: "esp-galaxy-coverage",
                    title: galPct !== null ? `Galaxy coverage: ${galExplored}/${galTotal} systems (${galPct.toFixed(1)}%) · ${galPhaseText}` : "Galaxy exploration percentage",
                    children: galPct !== null ? `GALAXY EXPLORED ${galPct.toFixed(1)}% (${galExplored}/${galTotal} · ${galPhaseText})` : "GALAXY EXPLORED --%"
                  }
                )
              ] }),
              /* @__PURE__ */ jsxRuntimeExports.jsxs("div", { className: "tab-group", role: "tablist", "aria-label": "Espionage subtabs", children: [
                /* @__PURE__ */ jsxRuntimeExports.jsx(
                  "button",
                  {
                    type: "button",
                    className: `tab-btn${espTab === "targets" ? " active" : ""}`,
                    "data-esp-tab": "targets",
                    onClick: () => setUiPref("espTab", "targets"),
                    children: "Spied Targets"
                  }
                ),
                /* @__PURE__ */ jsxRuntimeExports.jsx(
                  "button",
                  {
                    type: "button",
                    className: `tab-btn${espTab === "reports" ? " active" : ""}`,
                    "data-esp-tab": "reports",
                    onClick: () => setUiPref("espTab", "reports"),
                    children: "Historical Spy Reports"
                  }
                )
              ] })
            ] }),
            /* @__PURE__ */ jsxRuntimeExports.jsx("div", { className: "intel-pane", id: "esp-pane-targets", hidden: espTab !== "targets", children: /* @__PURE__ */ jsxRuntimeExports.jsx("div", { className: "table-wrap table-scroll", children: /* @__PURE__ */ jsxRuntimeExports.jsxs("table", { children: [
              /* @__PURE__ */ jsxRuntimeExports.jsx("thead", { children: /* @__PURE__ */ jsxRuntimeExports.jsxs("tr", { children: [
                /* @__PURE__ */ jsxRuntimeExports.jsx("th", { children: "Coords" }),
                /* @__PURE__ */ jsxRuntimeExports.jsx("th", { children: "Player / Planet" }),
                /* @__PURE__ */ jsxRuntimeExports.jsx("th", { children: "Status" }),
                /* @__PURE__ */ jsxRuntimeExports.jsx("th", { className: "col-right", children: "Fields" }),
                /* @__PURE__ */ jsxRuntimeExports.jsx("th", { children: "Resources (Pig / Met / Kry / Spi)" }),
                /* @__PURE__ */ jsxRuntimeExports.jsx("th", { className: "col-right", children: "Loot SpEq" }),
                /* @__PURE__ */ jsxRuntimeExports.jsx("th", { children: "Fleet / Defense" }),
                /* @__PURE__ */ jsxRuntimeExports.jsx("th", { className: "col-right", children: "Ops" })
              ] }) }),
              /* @__PURE__ */ jsxRuntimeExports.jsx("tbody", { id: "esp-targets-tbody", children: targets.length === 0 ? /* @__PURE__ */ jsxRuntimeExports.jsx("tr", { children: /* @__PURE__ */ jsxRuntimeExports.jsx("td", { colSpan: 8, className: "empty-cell", children: "No espionage targets recorded yet. Run Scan Galaxy or Sync All Folders." }) }) : targets.map((t, idx) => {
                const coords = String(t.coords || t.target_coords || "—");
                const isSpacia = Boolean(t.is_spacia);
                const isInact = Boolean(t.is_inactive) || String(t.status_color || "") === "yellow";
                const tags = [
                  isSpacia ? "SPACIA" : isInact ? "INACTIVE" : String(t.status_color || "ACTIVE").toUpperCase()
                ];
                if (t.has_debris) tags.push("DEBRIS");
                if (t.dug_in) tags.push("DUG-IN");
                const uf = finiteNum(t.uninhabited_fields, null);
                const fStr = formatUnitDictShort(t.fleet, 3);
                const dStr = formatUnitDictShort(t.defense, 3);
                const fdTxt = t.has_spy_intel ? fStr === "None" && dStr === "None" ? "Clear (0)" : `F:${fStr} / D:${dStr}` : "—";
                return /* @__PURE__ */ jsxRuntimeExports.jsxs("tr", { children: [
                  /* @__PURE__ */ jsxRuntimeExports.jsx("td", { className: "tabular", children: coords }),
                  /* @__PURE__ */ jsxRuntimeExports.jsx("td", { children: [t.planet_name, t.owner].filter(Boolean).join(" · ") || "Uninhabited / Unknown" }),
                  /* @__PURE__ */ jsxRuntimeExports.jsx("td", { children: /* @__PURE__ */ jsxRuntimeExports.jsx(
                    "span",
                    {
                      className: `status-word${isSpacia || isInact ? "" : " is-warn"}`,
                      children: tags.join(" · ")
                    }
                  ) }),
                  /* @__PURE__ */ jsxRuntimeExports.jsx("td", { className: "col-right tabular", children: uf !== null && uf > 0 ? String(Math.round(uf)) : "—" }),
                  /* @__PURE__ */ jsxRuntimeExports.jsx("td", { className: "tabular", children: t.has_spy_intel ? formatResourcesInline(t.resources) : "Not spied yet" }),
                  /* @__PURE__ */ jsxRuntimeExports.jsx("td", { className: "col-right tabular", children: t.has_spy_intel ? fmtInt(Math.round(finiteNum(t.spice_eq, 0))) : "—" }),
                  /* @__PURE__ */ jsxRuntimeExports.jsx("td", { className: "tabular", children: fdTxt }),
                  /* @__PURE__ */ jsxRuntimeExports.jsx("td", { className: "col-right", children: /* @__PURE__ */ jsxRuntimeExports.jsxs("div", { className: "row-actions", children: [
                    /* @__PURE__ */ jsxRuntimeExports.jsx(
                      "button",
                      {
                        type: "button",
                        className: "btn btn-secondary btn-sm",
                        onClick: () => handleSpyTarget(coords),
                        children: "Spy"
                      }
                    ),
                    /* @__PURE__ */ jsxRuntimeExports.jsx(
                      "button",
                      {
                        type: "button",
                        className: "btn btn-secondary btn-sm",
                        onClick: () => handleSimTarget(t),
                        children: "Sim"
                      }
                    ),
                    /* @__PURE__ */ jsxRuntimeExports.jsx(
                      "button",
                      {
                        type: "button",
                        className: "btn btn-primary btn-sm",
                        onClick: () => handleAttackTarget(coords),
                        children: "Attack"
                      }
                    )
                  ] }) })
                ] }, `${coords}-${idx}`);
              }) })
            ] }) }) }),
            /* @__PURE__ */ jsxRuntimeExports.jsx("div", { className: "intel-pane", id: "esp-pane-reports", hidden: espTab !== "reports", children: /* @__PURE__ */ jsxRuntimeExports.jsx("div", { className: "table-wrap table-scroll", children: /* @__PURE__ */ jsxRuntimeExports.jsxs("table", { children: [
              /* @__PURE__ */ jsxRuntimeExports.jsx("thead", { children: /* @__PURE__ */ jsxRuntimeExports.jsxs("tr", { children: [
                /* @__PURE__ */ jsxRuntimeExports.jsx("th", { children: "Recorded" }),
                /* @__PURE__ */ jsxRuntimeExports.jsx("th", { children: "Coords" }),
                /* @__PURE__ */ jsxRuntimeExports.jsx("th", { children: "Owner / Planet" }),
                /* @__PURE__ */ jsxRuntimeExports.jsx("th", { className: "col-right", children: "Fields" }),
                /* @__PURE__ */ jsxRuntimeExports.jsx("th", { children: "Resources & SpEq" }),
                /* @__PURE__ */ jsxRuntimeExports.jsx("th", { children: "Buildings / Techs" }),
                /* @__PURE__ */ jsxRuntimeExports.jsx("th", { children: "Fleet / Defense" })
              ] }) }),
              /* @__PURE__ */ jsxRuntimeExports.jsx("tbody", { id: "esp-reports-tbody", children: reports.length === 0 ? /* @__PURE__ */ jsxRuntimeExports.jsx("tr", { children: /* @__PURE__ */ jsxRuntimeExports.jsx("td", { colSpan: 7, className: "empty-cell", children: "No historical spy reports stored yet." }) }) : reports.map((r, idx) => {
                const uf = finiteNum(r.uninhabited_fields, null);
                const spEq = Math.round(finiteNum(r.spice_eq ?? r.loot_spice_eq, 0));
                const bStr = formatUnitDictShort(r.buildings, 3);
                const rStr = formatUnitDictShort(r.researches, 3);
                const fStr = formatUnitDictShort(r.fleet, 3);
                const dStr = formatUnitDictShort(r.defense, 3);
                return /* @__PURE__ */ jsxRuntimeExports.jsxs("tr", { children: [
                  /* @__PURE__ */ jsxRuntimeExports.jsx("td", { className: "tabular", children: fmtShortDateTime(r.ts, r.msg_date_text) }),
                  /* @__PURE__ */ jsxRuntimeExports.jsx("td", { className: "tabular", children: String(r.coords || "—") }),
                  /* @__PURE__ */ jsxRuntimeExports.jsx("td", { children: [r.planet_name, r.owner].filter(Boolean).join(" · ") || (r.uninhabited_fields ? "Uninhabited Slot" : "Unknown") }),
                  /* @__PURE__ */ jsxRuntimeExports.jsx("td", { className: "col-right tabular", children: uf !== null && uf > 0 ? `${Math.round(uf)} fields` : "—" }),
                  /* @__PURE__ */ jsxRuntimeExports.jsx("td", { className: "tabular", children: `${formatResourcesInline(r.resources)} (${fmtInt(spEq)} SpEq)` }),
                  /* @__PURE__ */ jsxRuntimeExports.jsx("td", { className: "tabular", children: bStr === "None" && rStr === "None" ? "—" : `B:${bStr} · R:${rStr}` }),
                  /* @__PURE__ */ jsxRuntimeExports.jsx("td", { className: "tabular", children: fStr === "None" && dStr === "None" ? "Clear (0)" : `F:${fStr} · D:${dStr}` })
                ] }, r.id ?? `${r.coords}-${idx}`);
              }) })
            ] }) }) })
          ] })
        ]
      }
    );
  };
  function attemptOutcomeFor(eventType, minFields) {
    const ev = String(eventType ?? "").trim().toLowerCase();
    if (ev.includes("delet") || ev.includes("abandon") || ev.includes("undersized")) {
      return { label: `DELETED <${minFields}`, cls: "is-loss" };
    }
    if (ev.includes("kept") || ev.includes("keep")) {
      return { label: `KEPT ≥${minFields}`, cls: "is-win" };
    }
    if (ev.includes("fail") || ev.includes("reject") || ev.includes("abort") || ev.includes("block") || ev.includes("error")) {
      return { label: "FAILED", cls: "is-loss" };
    }
    if (ev.includes("settl")) {
      return { label: "SETTLED", cls: "is-draw" };
    }
    if (ev.includes("dispatch") || ev.includes("launch") || ev.includes("sent")) {
      return { label: "DISPATCHED", cls: "is-pending" };
    }
    return { label: ev ? ev.toUpperCase().replace(/_/g, " ") : "UNKNOWN", cls: "is-pending" };
  }
  function attemptShortTag(stats, minFields) {
    const tried = Math.max(0, Math.round(finiteNum(stats.attempts_count, 0)));
    const lastFields = finiteNum(stats.last_fields_rolled, null);
    const outcome = attemptOutcomeFor(stats.last_event_type, minFields);
    const lastBits = [];
    if (lastFields !== null && lastFields > 0) lastBits.push(`${Math.round(lastFields)}f`);
    if (stats.last_event_type) lastBits.push(outcome.label.split(" ")[0].toLowerCase());
    return lastBits.length > 0 ? `Tried ${tried}x · Last: ${lastBits.join(" ")}` : `Tried ${tried}x`;
  }
  const ColoniesView = ({ hidden }) => {
    var _a, _b, _c;
    const {
      dashboard,
      activePage,
      activePlanet,
      colonyData,
      refreshColoniesView,
      syncAllInboxFolders,
      showFeedback,
      apiCommand: apiCommand2,
      loadDashboard,
      uiPrefs,
      setUiPref
    } = useConsole();
    const isHidden = hidden ?? activePage !== "colonies";
    const colFilter = uiPrefs.colonyFilter || "all";
    const [minFieldsInput, setMinFieldsInput] = reactExports.useState("300");
    const [allowColonizeInput, setAllowColonizeInput] = reactExports.useState(false);
    reactExports.useEffect(() => {
      var _a2, _b2;
      const cfgMin = (colonyData == null ? void 0 : colonyData.min_colony_fields) ?? ((_a2 = dashboard == null ? void 0 : dashboard.config) == null ? void 0 : _a2.min_colony_fields);
      if (cfgMin != null) {
        setMinFieldsInput(String(cfgMin));
      }
      const cfgAllow = (colonyData == null ? void 0 : colonyData.allow_colonize) ?? ((_b2 = dashboard == null ? void 0 : dashboard.config) == null ? void 0 : _b2.allow_colonize);
      if (cfgAllow != null) {
        setAllowColonizeInput(Boolean(cfgAllow));
      }
    }, [
      colonyData == null ? void 0 : colonyData.allow_colonize,
      colonyData == null ? void 0 : colonyData.min_colony_fields,
      (_a = dashboard == null ? void 0 : dashboard.config) == null ? void 0 : _a.allow_colonize,
      (_b = dashboard == null ? void 0 : dashboard.config) == null ? void 0 : _b.min_colony_fields
    ]);
    const gc = (colonyData == null ? void 0 : colonyData.grade_counts) || {};
    const allCands = Array.isArray(colonyData == null ? void 0 : colonyData.candidates) ? colonyData.candidates : [];
    const filteredCands = reactExports.useMemo(() => {
      if (colFilter === "probed") {
        return allCands.filter((c) => Boolean(c.probed));
      }
      if (colFilter === "qualified") {
        return allCands.filter((c) => Boolean(c.meets_target));
      }
      if (colFilter === "unprobed") {
        return allCands.filter((c) => !c.probed);
      }
      return allCands;
    }, [allCands, colFilter]);
    const minFieldsTarget = Math.max(
      1,
      Math.round(
        finiteNum((colonyData == null ? void 0 : colonyData.min_colony_fields) ?? ((_c = dashboard == null ? void 0 : dashboard.config) == null ? void 0 : _c.min_colony_fields), 300)
      )
    );
    const attempts = Array.isArray(colonyData == null ? void 0 : colonyData.colony_attempts) ? colonyData.colony_attempts.filter(
      (a) => a && typeof a === "object"
    ) : [];
    const attemptsSummary = (colonyData == null ? void 0 : colonyData.colony_attempts_summary) && typeof colonyData.colony_attempts_summary === "object" ? colonyData.colony_attempts_summary : {};
    const attemptsByCoords = (colonyData == null ? void 0 : colonyData.attempts_by_coords) && typeof colonyData.attempts_by_coords === "object" ? colonyData.attempts_by_coords : attemptsSummary.by_coords && typeof attemptsSummary.by_coords === "object" ? attemptsSummary.by_coords : {};
    const triedCount = Math.round(
      finiteNum(attemptsSummary.unique_coords_tried, Object.keys(attemptsByCoords).length)
    );
    const deletedCount = Math.round(
      finiteNum(
        attemptsSummary.deleted_undersized_count,
        attempts.filter((a) => attemptOutcomeFor(a.event_type, minFieldsTarget).label.startsWith("DELETED")).length
      )
    );
    const keptCount = Math.round(finiteNum(attemptsSummary.kept_count, 0));
    const totalEvents = Math.round(finiteNum(attemptsSummary.total_events, attempts.length));
    const handleSavePolicy = async () => {
      const minVal = Math.max(50, Math.min(600, Math.round(finiteNum(minFieldsInput, 300))));
      try {
        await apiCommand2({
          command: "update_config",
          config: { min_colony_fields: minVal, allow_colonize: allowColonizeInput }
        });
        showFeedback(
          `Colony policy saved: min ${minVal} fields, auto-colonize=${allowColonizeInput}.`,
          "ok"
        );
        await loadDashboard({ refresh: false, preserveConfig: false, force: true });
        await refreshColoniesView();
      } catch (err) {
        showFeedback(`Failed updating colony policy: ${err.message}`, feedbackKindForError(err));
      }
    };
    const handleScanGalaxy = async () => {
      try {
        showFeedback("Scanning Galaxy system for free slots...", "ok");
        const res = await apiCommand2({
          command: "scan_galaxy",
          planet_id: activePlanet == null ? void 0 : activePlanet.planet_id,
          dry_run: false
        });
        showFeedback(res.message || "Galaxy scan completed.", "ok");
        await loadDashboard({ refresh: false, force: true });
        await refreshColoniesView();
      } catch (err) {
        showFeedback(`Galaxy scan failed: ${err.message}`, feedbackKindForError(err));
      }
    };
    const handleProbeCandidate = async (coords) => {
      try {
        showFeedback(`Probing free slot ${coords} for field count...`, "ok");
        const res = await apiCommand2({
          command: "spy",
          target_coords: coords,
          quantity: 1,
          planet_id: activePlanet == null ? void 0 : activePlanet.planet_id,
          dry_run: false
        });
        showFeedback(
          res.message || `Spy probe dispatched to ${coords}.`,
          res.executed ? "ok" : "warn"
        );
        await refreshColoniesView();
      } catch (err) {
        showFeedback(`Probe failed: ${err.message}`, feedbackKindForError(err));
      }
    };
    const handleColonizeCandidate = async (coords) => {
      try {
        showFeedback(`Dispatching Colony ship (s8) to ${coords}...`, "ok");
        const res = await apiCommand2({
          command: "colonize",
          target_coords: coords,
          planet_id: activePlanet == null ? void 0 : activePlanet.planet_id,
          dry_run: false
        });
        showFeedback(
          res.message || `Colonization processed for ${coords}.`,
          res.executed ? "ok" : "warn"
        );
        await loadDashboard({ refresh: false, force: true });
        await refreshColoniesView();
      } catch (err) {
        showFeedback(`Colonize failed: ${err.message}`, feedbackKindForError(err));
      }
    };
    const handleDiscardCandidate = async (coords) => {
      try {
        const res = await apiCommand2({
          command: "discard_colony_candidate",
          target_coords: coords
        });
        showFeedback(
          res.message || `Discarded colony candidate ${coords}.`,
          "ok"
        );
        await refreshColoniesView();
        await loadDashboard({ refresh: false, force: true });
      } catch (err) {
        showFeedback(`Discard failed: ${err.message}`, feedbackKindForError(err));
      }
    };
    return /* @__PURE__ */ jsxRuntimeExports.jsxs(
      "section",
      {
        id: "view-colonies",
        className: "page-view",
        "aria-label": "Possible colonies and planetary field grading",
        hidden: isHidden,
        children: [
          /* @__PURE__ */ jsxRuntimeExports.jsxs("div", { className: "combat-top-cards col-top-cards", children: [
            /* @__PURE__ */ jsxRuntimeExports.jsxs("div", { className: "kpi-card", children: [
              /* @__PURE__ */ jsxRuntimeExports.jsx("span", { className: "kpi-label tabular", children: "Qualified 300+ Colonies" }),
              /* @__PURE__ */ jsxRuntimeExports.jsx("strong", { className: "kpi-value tabular", id: "col-kpi-qualified", children: `${fmtInt((colonyData == null ? void 0 : colonyData.qualified_count) ?? 0)} Ready` }),
              /* @__PURE__ */ jsxRuntimeExports.jsx("span", { className: "kpi-sub tabular", id: "col-kpi-grades", children: colonyData ? `S:${gc.S || 0} · A+:${gc["A+"] || 0} · A:${gc.A || 0} · B:${gc.B || 0} · C/D:${(gc.C || 0) + (gc.D || 0)}` : "S:0 · A+:0 · A:0 · B:0" })
            ] }),
            /* @__PURE__ */ jsxRuntimeExports.jsxs("div", { className: "kpi-card", children: [
              /* @__PURE__ */ jsxRuntimeExports.jsx("span", { className: "kpi-label tabular", children: "Scouted Free Slots" }),
              /* @__PURE__ */ jsxRuntimeExports.jsx("strong", { className: "kpi-value tabular", id: "col-kpi-probed", children: colonyData ? `${fmtInt(colonyData.probed_count ?? 0)} Spied` : "0 Probed" }),
              /* @__PURE__ */ jsxRuntimeExports.jsx("span", { className: "kpi-sub tabular", id: "col-kpi-unprobed", children: `${fmtInt((colonyData == null ? void 0 : colonyData.unprobed_count) ?? 0)} unprobed free slots` })
            ] }),
            /* @__PURE__ */ jsxRuntimeExports.jsxs("div", { className: "kpi-card", children: [
              /* @__PURE__ */ jsxRuntimeExports.jsx("span", { className: "kpi-label tabular", children: "Colonization Fleet" }),
              /* @__PURE__ */ jsxRuntimeExports.jsx("strong", { className: "kpi-value tabular", id: "col-kpi-ships", children: `${fmtInt((colonyData == null ? void 0 : colonyData.colony_ships) ?? 0)} s8 · ${fmtInt((colonyData == null ? void 0 : colonyData.spy_probes) ?? 0)} s10` }),
              /* @__PURE__ */ jsxRuntimeExports.jsx("span", { className: "kpi-sub tabular", id: "col-kpi-origin", children: colonyData ? `Origin [${colonyData.origin_coords || "--"}] · ${fmtInt(colonyData.planet_count ?? 1)} colonies` : "Origin [--]" })
            ] }),
            /* @__PURE__ */ jsxRuntimeExports.jsxs("div", { className: "kpi-card", children: [
              /* @__PURE__ */ jsxRuntimeExports.jsx("span", { className: "kpi-label tabular", children: `Tried / Deleted (<${minFieldsTarget})` }),
              /* @__PURE__ */ jsxRuntimeExports.jsx("strong", { className: "kpi-value tabular", id: "col-kpi-tried", children: `${fmtInt(triedCount)} Tried · ${fmtInt(deletedCount)} Del` }),
              /* @__PURE__ */ jsxRuntimeExports.jsx("span", { className: "kpi-sub tabular", id: "col-kpi-tried-sub", children: `${fmtInt(keptCount)} kept · ${fmtInt(totalEvents)} logged events` })
            ] }),
            /* @__PURE__ */ jsxRuntimeExports.jsxs("div", { className: "kpi-card", children: [
              /* @__PURE__ */ jsxRuntimeExports.jsx("span", { className: "kpi-label tabular", children: "Colony Policy" }),
              /* @__PURE__ */ jsxRuntimeExports.jsxs("div", { className: "colony-policy-controls", children: [
                /* @__PURE__ */ jsxRuntimeExports.jsxs("label", { className: "colony-inline-field tabular", htmlFor: "col-min-fields", children: [
                  "Min Fields",
                  /* @__PURE__ */ jsxRuntimeExports.jsx(
                    "input",
                    {
                      type: "number",
                      id: "col-min-fields",
                      min: 50,
                      max: 600,
                      step: 10,
                      value: minFieldsInput,
                      onChange: (e) => setMinFieldsInput(e.target.value)
                    }
                  )
                ] }),
                /* @__PURE__ */ jsxRuntimeExports.jsxs("label", { className: "toggle-chip colony-inline-toggle", children: [
                  /* @__PURE__ */ jsxRuntimeExports.jsx(
                    "input",
                    {
                      type: "checkbox",
                      id: "col-allow-colonize",
                      checked: allowColonizeInput,
                      onChange: (e) => setAllowColonizeInput(e.target.checked)
                    }
                  ),
                  " ",
                  "Auto Colonize"
                ] }),
                /* @__PURE__ */ jsxRuntimeExports.jsx(
                  "button",
                  {
                    type: "button",
                    className: "btn btn-primary btn-sm",
                    id: "btn-col-save",
                    onClick: handleSavePolicy,
                    children: "Apply"
                  }
                ),
                /* @__PURE__ */ jsxRuntimeExports.jsx(
                  "button",
                  {
                    type: "button",
                    className: "btn btn-secondary btn-sm",
                    id: "btn-col-scan",
                    onClick: handleScanGalaxy,
                    children: "Scan Galaxy"
                  }
                )
              ] })
            ] })
          ] }),
          /* @__PURE__ */ jsxRuntimeExports.jsxs("article", { className: "hud-window page-fill col-candidates-window", children: [
            /* @__PURE__ */ jsxRuntimeExports.jsxs("div", { className: "window-bar window-bar-wrap", children: [
              /* @__PURE__ */ jsxRuntimeExports.jsxs("div", { className: "window-title-group", children: [
                /* @__PURE__ */ jsxRuntimeExports.jsx("span", { className: "module-code tabular", children: "COL" }),
                /* @__PURE__ */ jsxRuntimeExports.jsx("h2", { children: "Possible Colonies & Spied Field Grading" }),
                /* @__PURE__ */ jsxRuntimeExports.jsx("span", { className: "module-meta tabular", id: "col-meta-count", children: colonyData ? `${filteredCands.length} / ${allCands.length} SLOTS` : "0 CANDIDATES" }),
                (dashboard == null ? void 0 : dashboard.galaxy_sweep) ? /* @__PURE__ */ jsxRuntimeExports.jsx("span", { className: "gal-cov-inline tabular", id: "col-galaxy-coverage", children: `GALAXY EXPLORED ${Math.max(
                  0,
                  Math.min(100, finiteNum(dashboard.galaxy_sweep.coverage_pct, 0))
                ).toFixed(1)}% (${fmtInt(dashboard.galaxy_sweep.explored_systems ?? 0)}/${fmtInt(
                  dashboard.galaxy_sweep.total_systems ?? 400
                )})` }) : null
              ] }),
              /* @__PURE__ */ jsxRuntimeExports.jsxs("div", { className: "filter-toolbar", children: [
                /* @__PURE__ */ jsxRuntimeExports.jsxs("div", { className: "filter-chips", role: "group", "aria-label": "Colony candidate filters", children: [
                  /* @__PURE__ */ jsxRuntimeExports.jsx(
                    "button",
                    {
                      type: "button",
                      className: `chip-btn${colFilter === "all" ? " active" : ""}`,
                      "data-col-filter": "all",
                      onClick: () => setUiPref("colonyFilter", "all"),
                      children: "All Slots"
                    }
                  ),
                  /* @__PURE__ */ jsxRuntimeExports.jsx(
                    "button",
                    {
                      type: "button",
                      className: `chip-btn${colFilter === "probed" ? " active" : ""}`,
                      "data-col-filter": "probed",
                      onClick: () => setUiPref("colonyFilter", "probed"),
                      children: "Spied & Graded"
                    }
                  ),
                  /* @__PURE__ */ jsxRuntimeExports.jsx(
                    "button",
                    {
                      type: "button",
                      className: `chip-btn${colFilter === "qualified" ? " active" : ""}`,
                      "data-col-filter": "qualified",
                      onClick: () => setUiPref("colonyFilter", "qualified"),
                      children: "300+ Fields (S / A+)"
                    }
                  ),
                  /* @__PURE__ */ jsxRuntimeExports.jsx(
                    "button",
                    {
                      type: "button",
                      className: `chip-btn${colFilter === "unprobed" ? " active" : ""}`,
                      "data-col-filter": "unprobed",
                      onClick: () => setUiPref("colonyFilter", "unprobed"),
                      children: "Unprobed (?)"
                    }
                  )
                ] }),
                /* @__PURE__ */ jsxRuntimeExports.jsx(
                  "button",
                  {
                    type: "button",
                    className: "btn btn-secondary btn-sm",
                    id: "btn-col-sync",
                    onClick: syncAllInboxFolders,
                    children: "Sync Spy Reports"
                  }
                )
              ] })
            ] }),
            /* @__PURE__ */ jsxRuntimeExports.jsx("div", { className: "table-wrap table-scroll", children: /* @__PURE__ */ jsxRuntimeExports.jsxs("table", { children: [
              /* @__PURE__ */ jsxRuntimeExports.jsx("thead", { children: /* @__PURE__ */ jsxRuntimeExports.jsxs("tr", { children: [
                /* @__PURE__ */ jsxRuntimeExports.jsx("th", { children: "Grade" }),
                /* @__PURE__ */ jsxRuntimeExports.jsx("th", { className: "col-right", children: "Score" }),
                /* @__PURE__ */ jsxRuntimeExports.jsx("th", { children: "Coords" }),
                /* @__PURE__ */ jsxRuntimeExports.jsx("th", { children: "Orbital Zone" }),
                /* @__PURE__ */ jsxRuntimeExports.jsx("th", { className: "col-right", children: "Fields" }),
                /* @__PURE__ */ jsxRuntimeExports.jsx("th", { className: "col-right", children: "Distance" }),
                /* @__PURE__ */ jsxRuntimeExports.jsx("th", { children: "Assessment" }),
                /* @__PURE__ */ jsxRuntimeExports.jsx("th", { className: "col-right", children: "Actions" })
              ] }) }),
              /* @__PURE__ */ jsxRuntimeExports.jsx("tbody", { id: "col-candidates-tbody", children: filteredCands.length === 0 ? /* @__PURE__ */ jsxRuntimeExports.jsx("tr", { children: /* @__PURE__ */ jsxRuntimeExports.jsx("td", { colSpan: 8, className: "empty-cell", children: colonyData ? "No matching colony slots. Click Scan Galaxy or probe free slots 7-12." : "Scan neighbor systems or sync spy reports to grade free planet slots." }) }) : filteredCands.map((c) => {
                const coords = String(c.coords || "—");
                const grade = String(c.grade || "?");
                const uf = finiteNum(c.uninhabited_fields, null);
                const cMin = finiteNum(c.colonize_min_fields, null);
                const cMax = finiteNum(c.colonize_max_fields, null);
                const fieldsDisp = typeof c.fields_display === "string" && c.fields_display.trim() ? c.fields_display.trim() : uf !== null && uf > 0 ? `${Math.round(uf)} fields` : cMax !== null && cMax > 0 ? cMin !== null && cMin > 0 && cMin !== cMax ? `${Math.round(cMin)}–${Math.round(cMax)} fields` : `${Math.round(cMax)} fields` : "";
                const sysDist = Math.round(finiteNum(c.system_distance, 0));
                const trialStats = attemptsByCoords[coords];
                const trialDeleted = trialStats && Math.round(finiteNum(trialStats.deleted_count, 0)) > 0;
                return /* @__PURE__ */ jsxRuntimeExports.jsxs("tr", { children: [
                  /* @__PURE__ */ jsxRuntimeExports.jsx("td", { children: /* @__PURE__ */ jsxRuntimeExports.jsx("span", { className: `grade-badge ${gradeClassFor(grade)}`, children: grade }) }),
                  /* @__PURE__ */ jsxRuntimeExports.jsx("td", { className: "col-right tabular", children: `${Math.round(finiteNum(c.score, 0))}/100` }),
                  /* @__PURE__ */ jsxRuntimeExports.jsxs("td", { className: "tabular", children: [
                    coords,
                    trialStats ? /* @__PURE__ */ jsxRuntimeExports.jsx(
                      "span",
                      {
                        className: `rate-chip col-trial-chip${trialDeleted ? " is-deleted" : ""}`,
                        "data-trial-coords": coords,
                        title: "Colonization attempts recorded in SQLite colony_attempts",
                        children: attemptShortTag(trialStats, minFieldsTarget)
                      }
                    ) : null
                  ] }),
                  /* @__PURE__ */ jsxRuntimeExports.jsx("td", { children: `Pos ${c.position || "?"} · ${c.orbital_band || "Unknown"}` }),
                  /* @__PURE__ */ jsxRuntimeExports.jsx("td", { className: "col-right tabular", children: fieldsDisp ? /* @__PURE__ */ jsxRuntimeExports.jsx("span", { className: `status-word${c.meets_target ? "" : " is-warn"}`, children: fieldsDisp }) : "Unprobed" }),
                  /* @__PURE__ */ jsxRuntimeExports.jsx("td", { className: "col-right tabular", children: sysDist === 0 ? "Same sys" : `${sysDist} sys` }),
                  /* @__PURE__ */ jsxRuntimeExports.jsx("td", { children: String(c.reason || "") }),
                  /* @__PURE__ */ jsxRuntimeExports.jsx("td", { className: "col-right", children: /* @__PURE__ */ jsxRuntimeExports.jsxs("div", { className: "row-actions", children: [
                    /* @__PURE__ */ jsxRuntimeExports.jsx(
                      "button",
                      {
                        type: "button",
                        className: "btn btn-secondary btn-sm",
                        onClick: () => handleProbeCandidate(coords),
                        children: c.probed ? "Re-Probe" : "Probe"
                      }
                    ),
                    /* @__PURE__ */ jsxRuntimeExports.jsx(
                      "button",
                      {
                        type: "button",
                        className: c.meets_target ? "btn btn-primary btn-sm" : "btn btn-secondary btn-sm",
                        onClick: () => handleColonizeCandidate(coords),
                        children: "Colonize"
                      }
                    ),
                    /* @__PURE__ */ jsxRuntimeExports.jsx(
                      "button",
                      {
                        type: "button",
                        className: "btn btn-warning btn-sm",
                        onClick: () => handleDiscardCandidate(coords),
                        children: "Discard"
                      }
                    )
                  ] }) })
                ] }, coords);
              }) })
            ] }) })
          ] }),
          /* @__PURE__ */ jsxRuntimeExports.jsxs("article", { className: "hud-window col-attempts-window", "aria-labelledby": "col-attempts-title", children: [
            /* @__PURE__ */ jsxRuntimeExports.jsx("div", { className: "window-bar window-bar-wrap", children: /* @__PURE__ */ jsxRuntimeExports.jsxs("div", { className: "window-title-group", children: [
              /* @__PURE__ */ jsxRuntimeExports.jsx("span", { className: "module-code tabular", children: "TRY" }),
              /* @__PURE__ */ jsxRuntimeExports.jsx("h2", { id: "col-attempts-title", children: "Tried Planets & Colonization Attempts" }),
              /* @__PURE__ */ jsxRuntimeExports.jsx("span", { className: "module-meta tabular", id: "col-attempts-meta", children: `${fmtInt(attempts.length)} EVENTS · ${fmtInt(triedCount)} COORDS` })
            ] }) }),
            /* @__PURE__ */ jsxRuntimeExports.jsx("div", { className: "table-wrap table-scroll", children: /* @__PURE__ */ jsxRuntimeExports.jsxs("table", { id: "colonies-attempts-table", children: [
              /* @__PURE__ */ jsxRuntimeExports.jsx("thead", { children: /* @__PURE__ */ jsxRuntimeExports.jsxs("tr", { children: [
                /* @__PURE__ */ jsxRuntimeExports.jsx("th", { children: "Coords" }),
                /* @__PURE__ */ jsxRuntimeExports.jsx("th", { children: "Outcome" }),
                /* @__PURE__ */ jsxRuntimeExports.jsx("th", { className: "col-right", children: "Rolled Fields" }),
                /* @__PURE__ */ jsxRuntimeExports.jsx("th", { className: "col-right", children: "Target / Expected" }),
                /* @__PURE__ */ jsxRuntimeExports.jsx("th", { className: "col-right", children: "Temp" }),
                /* @__PURE__ */ jsxRuntimeExports.jsx("th", { children: "Origin" }),
                /* @__PURE__ */ jsxRuntimeExports.jsx("th", { className: "col-right", children: "Time" }),
                /* @__PURE__ */ jsxRuntimeExports.jsx("th", { children: "Details / Reason" })
              ] }) }),
              /* @__PURE__ */ jsxRuntimeExports.jsx("tbody", { id: "colonies-attempts-tbody", children: attempts.length === 0 ? /* @__PURE__ */ jsxRuntimeExports.jsx("tr", { children: /* @__PURE__ */ jsxRuntimeExports.jsx("td", { colSpan: 8, className: "empty-cell", children: "No colonization attempts recorded yet. Every Colony ship dispatch, kept 300+ roll, and deleted undersized roll is logged here." }) }) : attempts.map((a, idx) => {
                const aCoords = String(a.target_coords || "—");
                const aMin = Math.round(finiteNum(a.min_fields_target, minFieldsTarget));
                const outcome = attemptOutcomeFor(a.event_type, aMin);
                const rolled = finiteNum(a.fields_rolled, null);
                const expMin = finiteNum(a.expected_min_fields, null);
                const expMax = finiteNum(a.expected_max_fields, null);
                const expTxt = expMax !== null && expMax > 0 ? expMin !== null && expMin > 0 && expMin !== expMax ? `${Math.round(expMin)}–${Math.round(expMax)}` : `${Math.round(expMax)}` : "";
                const temp = finiteNum(a.temperature, null);
                const reasonTxt = String(a.status_message || "").trim() || (a.grade ? `Grade ${a.grade}` : "") || "—";
                return /* @__PURE__ */ jsxRuntimeExports.jsxs(
                  "tr",
                  {
                    "data-attempt-event": String(a.event_type || ""),
                    children: [
                      /* @__PURE__ */ jsxRuntimeExports.jsx("td", { className: "tabular", children: aCoords }),
                      /* @__PURE__ */ jsxRuntimeExports.jsx("td", { children: /* @__PURE__ */ jsxRuntimeExports.jsx("span", { className: `outcome-badge ${outcome.cls}`, children: outcome.label }) }),
                      /* @__PURE__ */ jsxRuntimeExports.jsx("td", { className: "col-right tabular", children: rolled !== null && rolled > 0 ? /* @__PURE__ */ jsxRuntimeExports.jsx("span", { className: `status-word${rolled >= aMin ? "" : " is-warn"}`, children: `${Math.round(rolled)} f` }) : "—" }),
                      /* @__PURE__ */ jsxRuntimeExports.jsx("td", { className: "col-right tabular", children: expTxt ? `≥${aMin} · exp ${expTxt}` : `≥${aMin}` }),
                      /* @__PURE__ */ jsxRuntimeExports.jsx("td", { className: "col-right tabular", children: temp !== null ? `${Math.round(temp)} °C` : "—" }),
                      /* @__PURE__ */ jsxRuntimeExports.jsx("td", { className: "tabular", children: a.origin_coords ? String(a.origin_coords) : "—" }),
                      /* @__PURE__ */ jsxRuntimeExports.jsx("td", { className: "col-right tabular", children: fmtShortDateTime(a.ts) }),
                      /* @__PURE__ */ jsxRuntimeExports.jsx("td", { children: reasonTxt })
                    ]
                  },
                  `${a.id ?? idx}-${aCoords}`
                );
              }) })
            ] }) })
          ] })
        ]
      }
    );
  };
  const AttacksView = ({ hidden }) => {
    const {
      activePage,
      activePlanet,
      atkData,
      refreshAttacksView,
      syncAllInboxFolders,
      showFeedback,
      apiCommand: apiCommand2,
      uiPrefs,
      setUiPref
    } = useConsole();
    const isHidden = hidden ?? activePage !== "attacks";
    const atkTab = uiPrefs.atkTab === "blocks" ? "blocks" : "battles";
    const sum = (atkData == null ? void 0 : atkData.summary) || {};
    const rStats = (atkData == null ? void 0 : atkData.raid_stats) || {};
    const battles = Array.isArray(atkData == null ? void 0 : atkData.battles) ? atkData.battles : [];
    const blocks = Array.isArray(atkData == null ? void 0 : atkData.blocked_targets) ? atkData.blocked_targets : [];
    const raids24 = Math.round(finiteNum(rStats.total_24h, 0));
    const handleSpyBlockTarget = async (coords) => {
      try {
        const res = await apiCommand2({
          command: "spy",
          target_coords: coords,
          quantity: 1,
          planet_id: activePlanet == null ? void 0 : activePlanet.planet_id,
          dry_run: false
        });
        showFeedback(
          res.message || `Spy probe sent to ${coords}.`,
          res.executed ? "ok" : "warn"
        );
      } catch (err) {
        showFeedback(`Spy failed: ${err.message}`, feedbackKindForError(err));
      }
    };
    return /* @__PURE__ */ jsxRuntimeExports.jsxs(
      "section",
      {
        id: "view-attacks",
        className: "page-view",
        "aria-label": "Battle reports, plunder, and bash protection status",
        hidden: isHidden,
        children: [
          /* @__PURE__ */ jsxRuntimeExports.jsxs("div", { className: "combat-top-cards", children: [
            /* @__PURE__ */ jsxRuntimeExports.jsxs("div", { className: "kpi-card", children: [
              /* @__PURE__ */ jsxRuntimeExports.jsx("span", { className: "kpi-label tabular", children: "Recorded Battles" }),
              /* @__PURE__ */ jsxRuntimeExports.jsx("strong", { className: "kpi-value tabular", id: "atk-kpi-total", children: `${fmtInt(sum.total_battles ?? battles.length)} Battles` }),
              /* @__PURE__ */ jsxRuntimeExports.jsx("span", { className: "kpi-sub tabular", id: "atk-kpi-wl", children: `W ${sum.wins ?? 0} · L ${sum.losses ?? 0} · D ${sum.draws ?? 0}` })
            ] }),
            /* @__PURE__ */ jsxRuntimeExports.jsxs("div", { className: "kpi-card", children: [
              /* @__PURE__ */ jsxRuntimeExports.jsx("span", { className: "kpi-label tabular", children: "Plundered Resources" }),
              /* @__PURE__ */ jsxRuntimeExports.jsx("strong", { className: "kpi-value tabular", id: "atk-kpi-loot", children: `${fmtInt(Math.round(finiteNum(sum.total_loot_spice_eq, 0)))} SpEq` }),
              /* @__PURE__ */ jsxRuntimeExports.jsx("span", { className: "kpi-sub tabular", id: "atk-kpi-loss", children: `Attacker losses: ${fmtInt(Math.round(finiteNum(sum.total_losses_spice_eq, 0)))} SpEq` })
            ] }),
            /* @__PURE__ */ jsxRuntimeExports.jsxs("div", { className: "kpi-card", children: [
              /* @__PURE__ */ jsxRuntimeExports.jsx("span", { className: "kpi-label tabular", children: "24h Raid & Bash Status" }),
              /* @__PURE__ */ jsxRuntimeExports.jsx("strong", { className: "kpi-value tabular", id: "atk-kpi-raids24", children: `${fmtInt(raids24)} / 24h` }),
              /* @__PURE__ */ jsxRuntimeExports.jsx("span", { className: "kpi-sub tabular", id: "atk-kpi-blocks", children: atkData ? `${fmtInt(sum.active_blocks_count ?? 0)} active bash timeouts` : "0 active bash blocks" })
            ] }),
            /* @__PURE__ */ jsxRuntimeExports.jsxs("div", { className: "kpi-card", children: [
              /* @__PURE__ */ jsxRuntimeExports.jsx("span", { className: "kpi-label tabular", children: "Battle Report Sync" }),
              /* @__PURE__ */ jsxRuntimeExports.jsxs("div", { className: "combat-jumps", children: [
                /* @__PURE__ */ jsxRuntimeExports.jsx(
                  "button",
                  {
                    type: "button",
                    className: "btn btn-primary btn-sm",
                    id: "btn-atk-sync",
                    onClick: syncAllInboxFolders,
                    children: "Sync All Folders"
                  }
                ),
                /* @__PURE__ */ jsxRuntimeExports.jsx(
                  "button",
                  {
                    type: "button",
                    className: "btn btn-secondary btn-sm",
                    id: "btn-atk-refresh",
                    onClick: refreshAttacksView,
                    children: "Refresh DB"
                  }
                )
              ] })
            ] })
          ] }),
          /* @__PURE__ */ jsxRuntimeExports.jsxs("article", { className: "hud-window page-fill", children: [
            /* @__PURE__ */ jsxRuntimeExports.jsxs("div", { className: "window-bar window-bar-wrap", children: [
              /* @__PURE__ */ jsxRuntimeExports.jsxs("div", { className: "window-title-group", children: [
                /* @__PURE__ */ jsxRuntimeExports.jsx("span", { className: "module-code tabular", children: "ATK" }),
                /* @__PURE__ */ jsxRuntimeExports.jsx("h2", { children: "Combat Reports, Plunder & Bash Timeouts" }),
                /* @__PURE__ */ jsxRuntimeExports.jsx("span", { className: "module-meta tabular", id: "atk-meta-count", children: atkData ? `${battles.length} REPORTS · ${blocks.length} BLOCKS` : "0 REPORTS" })
              ] }),
              /* @__PURE__ */ jsxRuntimeExports.jsxs("div", { className: "tab-group", role: "tablist", "aria-label": "Attack view subtabs", children: [
                /* @__PURE__ */ jsxRuntimeExports.jsx(
                  "button",
                  {
                    type: "button",
                    className: `tab-btn${atkTab === "battles" ? " active" : ""}`,
                    "data-atk-tab": "battles",
                    onClick: () => setUiPref("atkTab", "battles"),
                    children: "Battle Reports"
                  }
                ),
                /* @__PURE__ */ jsxRuntimeExports.jsx(
                  "button",
                  {
                    type: "button",
                    className: `tab-btn${atkTab === "blocks" ? " active" : ""}`,
                    "data-atk-tab": "blocks",
                    onClick: () => setUiPref("atkTab", "blocks"),
                    children: "Bash & Server Blocks"
                  }
                )
              ] })
            ] }),
            /* @__PURE__ */ jsxRuntimeExports.jsx("div", { className: "intel-pane", id: "atk-pane-battles", hidden: atkTab !== "battles", children: /* @__PURE__ */ jsxRuntimeExports.jsx("div", { className: "table-wrap table-scroll", children: /* @__PURE__ */ jsxRuntimeExports.jsxs("table", { children: [
              /* @__PURE__ */ jsxRuntimeExports.jsx("thead", { children: /* @__PURE__ */ jsxRuntimeExports.jsxs("tr", { children: [
                /* @__PURE__ */ jsxRuntimeExports.jsx("th", { children: "Time" }),
                /* @__PURE__ */ jsxRuntimeExports.jsx("th", { children: "Target" }),
                /* @__PURE__ */ jsxRuntimeExports.jsx("th", { children: "Defender" }),
                /* @__PURE__ */ jsxRuntimeExports.jsx("th", { children: "Outcome" }),
                /* @__PURE__ */ jsxRuntimeExports.jsx("th", { className: "col-right", children: "Rounds" }),
                /* @__PURE__ */ jsxRuntimeExports.jsx("th", { children: "Plundered Loot" }),
                /* @__PURE__ */ jsxRuntimeExports.jsx("th", { children: "Losses (Atk / Def)" }),
                /* @__PURE__ */ jsxRuntimeExports.jsx("th", { children: "Debris Created" })
              ] }) }),
              /* @__PURE__ */ jsxRuntimeExports.jsx("tbody", { id: "atk-battles-tbody", children: battles.length === 0 ? /* @__PURE__ */ jsxRuntimeExports.jsx("tr", { children: /* @__PURE__ */ jsxRuntimeExports.jsx("td", { colSpan: 8, className: "empty-cell", children: "Click Sync All Folders to archive in-game battle reports." }) }) : battles.map((b, idx) => {
                var _a, _b, _c, _d, _e;
                const outVal = String(b.outcome || "draw").toLowerCase();
                const outCls = outVal === "win" ? "is-win" : outVal === "loss" ? "is-loss" : "is-draw";
                const lootSp = Math.round(finiteNum((_a = b.loot) == null ? void 0 : _a.spice_eq, 0));
                const atkLoss = Math.round(finiteNum((_b = b.attacker_losses) == null ? void 0 : _b.spice_eq, 0));
                const defLoss = Math.round(finiteNum((_c = b.defender_losses) == null ? void 0 : _c.spice_eq, 0));
                const dPig = Math.round(finiteNum((_d = b.debris) == null ? void 0 : _d.pig_iron, 0));
                const dMet = Math.round(finiteNum((_e = b.debris) == null ? void 0 : _e.metal, 0));
                return /* @__PURE__ */ jsxRuntimeExports.jsxs("tr", { children: [
                  /* @__PURE__ */ jsxRuntimeExports.jsx("td", { className: "tabular", children: fmtShortDateTime(b.ts, b.msg_date_text) }),
                  /* @__PURE__ */ jsxRuntimeExports.jsx("td", { className: "tabular", children: String(b.coords || "—") }),
                  /* @__PURE__ */ jsxRuntimeExports.jsx("td", { children: String(b.defender_name || b.subject || "Unknown") }),
                  /* @__PURE__ */ jsxRuntimeExports.jsx("td", { children: /* @__PURE__ */ jsxRuntimeExports.jsx("span", { className: `outcome-badge ${outCls}`, children: outVal.toUpperCase() }) }),
                  /* @__PURE__ */ jsxRuntimeExports.jsx("td", { className: "col-right tabular", children: String(Math.round(finiteNum(b.rounds, 0))) }),
                  /* @__PURE__ */ jsxRuntimeExports.jsx("td", { className: "tabular", children: lootSp > 0 ? `${formatResourcesInline(b.loot)} (+${fmtInt(lootSp)} SpEq)` : "0" }),
                  /* @__PURE__ */ jsxRuntimeExports.jsx("td", { className: "tabular", children: `Atk ${fmtInt(atkLoss)} / Def ${fmtInt(defLoss)} SpEq` }),
                  /* @__PURE__ */ jsxRuntimeExports.jsx("td", { className: "tabular", children: dPig > 0 || dMet > 0 ? `${fmtInt(dPig)} Pig · ${fmtInt(dMet)} Met` : "None" })
                ] }, b.id ?? `${b.coords}-${idx}`);
              }) })
            ] }) }) }),
            /* @__PURE__ */ jsxRuntimeExports.jsx("div", { className: "intel-pane", id: "atk-pane-blocks", hidden: atkTab !== "blocks", children: /* @__PURE__ */ jsxRuntimeExports.jsx("div", { className: "table-wrap table-scroll", children: /* @__PURE__ */ jsxRuntimeExports.jsxs("table", { children: [
              /* @__PURE__ */ jsxRuntimeExports.jsx("thead", { children: /* @__PURE__ */ jsxRuntimeExports.jsxs("tr", { children: [
                /* @__PURE__ */ jsxRuntimeExports.jsx("th", { children: "Coords" }),
                /* @__PURE__ */ jsxRuntimeExports.jsx("th", { children: "Owner / Planet" }),
                /* @__PURE__ */ jsxRuntimeExports.jsx("th", { className: "col-right", children: "Bash Hits" }),
                /* @__PURE__ */ jsxRuntimeExports.jsx("th", { className: "col-right", children: "Cooldown Left" }),
                /* @__PURE__ */ jsxRuntimeExports.jsx("th", { children: "Server Protection Reason" }),
                /* @__PURE__ */ jsxRuntimeExports.jsx("th", { className: "col-right", children: "Ops" })
              ] }) }),
              /* @__PURE__ */ jsxRuntimeExports.jsx("tbody", { id: "atk-blocks-tbody", children: blocks.length === 0 ? /* @__PURE__ */ jsxRuntimeExports.jsx("tr", { children: /* @__PURE__ */ jsxRuntimeExports.jsx("td", { colSpan: 6, className: "empty-cell", children: "No active bash protection or server blocks recorded." }) }) : blocks.map((bt, idx) => {
                const coords = String(bt.coords || "—");
                return /* @__PURE__ */ jsxRuntimeExports.jsxs("tr", { children: [
                  /* @__PURE__ */ jsxRuntimeExports.jsx("td", { className: "tabular", children: coords }),
                  /* @__PURE__ */ jsxRuntimeExports.jsx("td", { children: [bt.planet_name, bt.owner].filter(Boolean).join(" · ") || "Unknown" }),
                  /* @__PURE__ */ jsxRuntimeExports.jsx("td", { className: "col-right tabular", children: String(Math.round(finiteNum(bt.bash_hits, 0))) }),
                  /* @__PURE__ */ jsxRuntimeExports.jsx("td", { className: "col-right tabular", children: bt.active_block ? fmtDurationCountdown(bt.remaining_seconds) : "Cleared" }),
                  /* @__PURE__ */ jsxRuntimeExports.jsx("td", { children: String(bt.block_reason || "Bash / Server protection") }),
                  /* @__PURE__ */ jsxRuntimeExports.jsx("td", { className: "col-right", children: /* @__PURE__ */ jsxRuntimeExports.jsx(
                    "button",
                    {
                      type: "button",
                      className: "btn btn-secondary btn-sm",
                      onClick: () => handleSpyBlockTarget(coords),
                      children: "Spy"
                    }
                  ) })
                ] }, `${coords}-${idx}`);
              }) })
            ] }) }) })
          ] })
        ]
      }
    );
  };
  const EMAIL_CATS = [
    { key: "all", label: "All Non-Spy/Battle" },
    { key: "general", label: "User / Alliance / Admin" },
    { key: "fleet_return", label: "Fleet Return" },
    { key: "fleet_transport", label: "Transport" },
    { key: "colonize_report", label: "Colonization" },
    { key: "counter_spy", label: "Counter-Spy" },
    { key: "recycle_report", label: "Recycle Raw" }
  ];
  const RecyclersView = ({ hidden }) => {
    const {
      activePage,
      activePlanet,
      recData,
      refreshRecyclersView,
      syncAllInboxFolders,
      showFeedback,
      apiCommand: apiCommand2,
      uiPrefs,
      setUiPref
    } = useConsole();
    const isHidden = hidden ?? activePage !== "recyclers";
    const recTab = uiPrefs.recTab === "debris" || uiPrefs.recTab === "emails" ? uiPrefs.recTab : "harvests";
    const emailCat = uiPrefs.emailCatFilter || "all";
    const sum = (recData == null ? void 0 : recData.summary) || {};
    const recycles = Array.isArray(recData == null ? void 0 : recData.recycles) ? recData.recycles : [];
    const debris = Array.isArray(recData == null ? void 0 : recData.debris_targets) ? recData.debris_targets : [];
    const emails = Array.isArray(recData == null ? void 0 : recData.other_emails) ? recData.other_emails : [];
    const cats = sum.category_counts || {};
    const tPig = Math.round(finiteNum(sum.total_pig_iron, 0));
    const tMet = Math.round(finiteNum(sum.total_metal, 0));
    const tKry = Math.round(finiteNum(sum.total_kryptonite, 0));
    const tSpi = Math.round(finiteNum(sum.total_spice, 0));
    const rawKpiText = tKry > 0 || tSpi > 0 ? `${fmtInt(tPig)} Pig · ${fmtInt(tMet)} Met · ${fmtInt(tKry)} Kry · ${fmtInt(tSpi)} Spi` : `${fmtInt(tPig)} Pig · ${fmtInt(tMet)} Met`;
    const userCnt = Math.round(finiteNum(cats.general, 0));
    const retCnt = Math.round(finiteNum(cats.fleet_return, 0));
    const handleSelectEmailCat = (cat) => {
      setUiPref("emailCatFilter", cat);
      refreshRecyclersView(cat);
    };
    const handleRecycleDebris = async (coords) => {
      try {
        showFeedback(`Dispatching Recyclers (s11) to ${coords}...`, "ok");
        const res = await apiCommand2({
          command: "recycle",
          target_coords: coords,
          planet_id: activePlanet == null ? void 0 : activePlanet.planet_id,
          dry_run: false
        });
        showFeedback(
          res.message || `Recycler dispatched to ${coords}.`,
          res.executed ? "ok" : "warn"
        );
        await refreshRecyclersView();
      } catch (err) {
        showFeedback(`Recycle failed: ${err.message}`, feedbackKindForError(err));
      }
    };
    return /* @__PURE__ */ jsxRuntimeExports.jsxs(
      "section",
      {
        id: "view-recyclers",
        className: "page-view",
        "aria-label": "Recycler harvests, floating debris, and archived emails",
        hidden: isHidden,
        children: [
          /* @__PURE__ */ jsxRuntimeExports.jsxs("div", { className: "combat-top-cards", children: [
            /* @__PURE__ */ jsxRuntimeExports.jsxs("div", { className: "kpi-card", children: [
              /* @__PURE__ */ jsxRuntimeExports.jsx("span", { className: "kpi-label tabular", children: "Recycler Runs" }),
              /* @__PURE__ */ jsxRuntimeExports.jsx("strong", { className: "kpi-value tabular", id: "rec-kpi-runs", children: `${fmtInt(sum.total_runs ?? recycles.length)} Harvests` }),
              /* @__PURE__ */ jsxRuntimeExports.jsx("span", { className: "kpi-sub tabular", id: "rec-kpi-ships", children: `${fmtInt(sum.recyclers_owned ?? 0)} Recyclers (s11) owned` })
            ] }),
            /* @__PURE__ */ jsxRuntimeExports.jsxs("div", { className: "kpi-card", children: [
              /* @__PURE__ */ jsxRuntimeExports.jsx("span", { className: "kpi-label tabular", children: "Total Rubble Collected" }),
              /* @__PURE__ */ jsxRuntimeExports.jsx("strong", { className: "kpi-value tabular", id: "rec-kpi-speq", children: `${fmtInt(Math.round(finiteNum(sum.total_spice_eq, 0)))} SpEq` }),
              /* @__PURE__ */ jsxRuntimeExports.jsx("span", { className: "kpi-sub tabular", id: "rec-kpi-raw", children: rawKpiText })
            ] }),
            /* @__PURE__ */ jsxRuntimeExports.jsxs("div", { className: "kpi-card", children: [
              /* @__PURE__ */ jsxRuntimeExports.jsx("span", { className: "kpi-label tabular", children: "Active Debris Fields" }),
              /* @__PURE__ */ jsxRuntimeExports.jsx("strong", { className: "kpi-value tabular", id: "rec-kpi-debris", children: `${fmtInt(sum.active_debris_count ?? debris.length)} Fields` }),
              /* @__PURE__ */ jsxRuntimeExports.jsx("span", { className: "kpi-sub tabular", id: "rec-kpi-debris-note", children: "Ready for s11 harvest" })
            ] }),
            /* @__PURE__ */ jsxRuntimeExports.jsxs("div", { className: "kpi-card", children: [
              /* @__PURE__ */ jsxRuntimeExports.jsx("span", { className: "kpi-label tabular", children: "Other Emails & Sync" }),
              /* @__PURE__ */ jsxRuntimeExports.jsx("strong", { className: "kpi-value tabular", id: "rec-kpi-emails", children: recData ? `${fmtInt(sum.other_email_count ?? emails.length)} Notices` : "0 Emails" }),
              /* @__PURE__ */ jsxRuntimeExports.jsxs("div", { className: "combat-jumps", children: [
                /* @__PURE__ */ jsxRuntimeExports.jsx("span", { className: "kpi-sub tabular", id: "rec-kpi-cats", children: recData ? `User ${userCnt} · Return ${retCnt}` : "User 0 · Fleet 0" }),
                /* @__PURE__ */ jsxRuntimeExports.jsx(
                  "button",
                  {
                    type: "button",
                    className: "btn btn-primary btn-sm",
                    id: "btn-rec-sync",
                    "data-testid": "btn-sync-all-messages",
                    onClick: syncAllInboxFolders,
                    children: "Sync All Folders"
                  }
                )
              ] })
            ] })
          ] }),
          /* @__PURE__ */ jsxRuntimeExports.jsxs("article", { className: "hud-window page-fill", children: [
            /* @__PURE__ */ jsxRuntimeExports.jsxs("div", { className: "window-bar window-bar-wrap", children: [
              /* @__PURE__ */ jsxRuntimeExports.jsxs("div", { className: "window-title-group", children: [
                /* @__PURE__ */ jsxRuntimeExports.jsx("span", { className: "module-code tabular", children: "REC" }),
                /* @__PURE__ */ jsxRuntimeExports.jsx("h2", { children: "Recycler Harvests, Debris & Inbox Emails" }),
                /* @__PURE__ */ jsxRuntimeExports.jsx("span", { className: "module-meta tabular", id: "rec-meta-count", children: recData ? `${recycles.length} HARVESTS · ${debris.length} DEBRIS · ${emails.length} EMAILS` : "0 HARVESTS" })
              ] }),
              /* @__PURE__ */ jsxRuntimeExports.jsxs("div", { className: "tab-group", role: "tablist", "aria-label": "Recycler and email subtabs", children: [
                /* @__PURE__ */ jsxRuntimeExports.jsx(
                  "button",
                  {
                    type: "button",
                    className: `tab-btn${recTab === "harvests" ? " active" : ""}`,
                    "data-rec-tab": "harvests",
                    onClick: () => setUiPref("recTab", "harvests"),
                    children: "Recycler Reports"
                  }
                ),
                /* @__PURE__ */ jsxRuntimeExports.jsx(
                  "button",
                  {
                    type: "button",
                    className: `tab-btn${recTab === "debris" ? " active" : ""}`,
                    "data-rec-tab": "debris",
                    onClick: () => setUiPref("recTab", "debris"),
                    children: "Active Debris"
                  }
                ),
                /* @__PURE__ */ jsxRuntimeExports.jsx(
                  "button",
                  {
                    type: "button",
                    className: `tab-btn${recTab === "emails" ? " active" : ""}`,
                    "data-rec-tab": "emails",
                    onClick: () => setUiPref("recTab", "emails"),
                    children: "Other Emails & Fleet"
                  }
                )
              ] })
            ] }),
            /* @__PURE__ */ jsxRuntimeExports.jsx("div", { className: "intel-pane", id: "rec-pane-harvests", hidden: recTab !== "harvests", children: /* @__PURE__ */ jsxRuntimeExports.jsx("div", { className: "table-wrap table-scroll", children: /* @__PURE__ */ jsxRuntimeExports.jsxs("table", { children: [
              /* @__PURE__ */ jsxRuntimeExports.jsx("thead", { children: /* @__PURE__ */ jsxRuntimeExports.jsxs("tr", { children: [
                /* @__PURE__ */ jsxRuntimeExports.jsx("th", { children: "Time" }),
                /* @__PURE__ */ jsxRuntimeExports.jsx("th", { children: "Debris Coords" }),
                /* @__PURE__ */ jsxRuntimeExports.jsx("th", { className: "col-right", children: "Recyclers / Cap" }),
                /* @__PURE__ */ jsxRuntimeExports.jsx("th", { className: "col-right", children: "Pig Iron" }),
                /* @__PURE__ */ jsxRuntimeExports.jsx("th", { className: "col-right", children: "Metal" }),
                /* @__PURE__ */ jsxRuntimeExports.jsx("th", { className: "col-right", children: "Harvest SpEq" }),
                /* @__PURE__ */ jsxRuntimeExports.jsx("th", { children: "Field Before" })
              ] }) }),
              /* @__PURE__ */ jsxRuntimeExports.jsx("tbody", { id: "rec-harvests-tbody", children: recycles.length === 0 ? /* @__PURE__ */ jsxRuntimeExports.jsx("tr", { children: /* @__PURE__ */ jsxRuntimeExports.jsx("td", { colSpan: 7, className: "empty-cell", children: "Click Sync All Folders to archive fleet recycling reports." }) }) : recycles.map((r, idx) => {
                const rCnt = Math.round(finiteNum(r.recycler_count, 0));
                const rCap = Math.round(finiteNum(r.cargo_capacity, 0));
                const bPig = Math.round(finiteNum(r.field_pig_iron, 0));
                const bMet = Math.round(finiteNum(r.field_metal, 0));
                const hKry = Math.round(finiteNum(r.harvested_kryptonite, 0));
                const hSpi = Math.round(finiteNum(r.harvested_spice, 0));
                const befText = hKry > 0 || hSpi > 0 ? `+${fmtInt(hKry)} Kry · +${fmtInt(hSpi)} Spi` : bPig > 0 || bMet > 0 ? `${fmtInt(bPig)} Pig · ${fmtInt(bMet)} Met` : "—";
                return /* @__PURE__ */ jsxRuntimeExports.jsxs("tr", { children: [
                  /* @__PURE__ */ jsxRuntimeExports.jsx("td", { className: "tabular", children: fmtShortDateTime(r.ts, r.msg_date_text) }),
                  /* @__PURE__ */ jsxRuntimeExports.jsx("td", { className: "tabular", children: String(r.coords || "—") }),
                  /* @__PURE__ */ jsxRuntimeExports.jsx("td", { className: "col-right tabular", children: rCnt > 0 ? `${fmtInt(rCnt)}x (${fmtInt(rCap)})` : "—" }),
                  /* @__PURE__ */ jsxRuntimeExports.jsx("td", { className: "col-right tabular", children: fmtInt(r.harvested_pig_iron ?? 0) }),
                  /* @__PURE__ */ jsxRuntimeExports.jsx("td", { className: "col-right tabular", children: fmtInt(r.harvested_metal ?? 0) }),
                  /* @__PURE__ */ jsxRuntimeExports.jsx("td", { className: "col-right tabular", children: `${fmtInt(Math.round(finiteNum(r.spice_eq, 0)))} SpEq` }),
                  /* @__PURE__ */ jsxRuntimeExports.jsx("td", { className: "tabular", children: befText })
                ] }, r.id ?? `${r.coords}-${idx}`);
              }) })
            ] }) }) }),
            /* @__PURE__ */ jsxRuntimeExports.jsx("div", { className: "intel-pane", id: "rec-pane-debris", hidden: recTab !== "debris", children: /* @__PURE__ */ jsxRuntimeExports.jsx("div", { className: "table-wrap table-scroll", children: /* @__PURE__ */ jsxRuntimeExports.jsxs("table", { children: [
              /* @__PURE__ */ jsxRuntimeExports.jsx("thead", { children: /* @__PURE__ */ jsxRuntimeExports.jsxs("tr", { children: [
                /* @__PURE__ */ jsxRuntimeExports.jsx("th", { children: "Coords" }),
                /* @__PURE__ */ jsxRuntimeExports.jsx("th", { children: "Planet / Owner" }),
                /* @__PURE__ */ jsxRuntimeExports.jsx("th", { children: "Status" }),
                /* @__PURE__ */ jsxRuntimeExports.jsx("th", { className: "col-right", children: "Last Seen" }),
                /* @__PURE__ */ jsxRuntimeExports.jsx("th", { className: "col-right", children: "Dispatch" })
              ] }) }),
              /* @__PURE__ */ jsxRuntimeExports.jsx("tbody", { id: "rec-debris-tbody", children: debris.length === 0 ? /* @__PURE__ */ jsxRuntimeExports.jsx("tr", { children: /* @__PURE__ */ jsxRuntimeExports.jsx("td", { colSpan: 5, className: "empty-cell", children: "No active floating debris fields spotted in scanned systems." }) }) : debris.map((d, idx) => {
                const coords = String(d.coords || "—");
                return /* @__PURE__ */ jsxRuntimeExports.jsxs("tr", { children: [
                  /* @__PURE__ */ jsxRuntimeExports.jsx("td", { className: "tabular", children: coords }),
                  /* @__PURE__ */ jsxRuntimeExports.jsx("td", { children: [d.planet_name, d.owner].filter(Boolean).join(" · ") || "Orbital Debris" }),
                  /* @__PURE__ */ jsxRuntimeExports.jsx("td", { children: /* @__PURE__ */ jsxRuntimeExports.jsx("span", { className: "status-word", children: "RUBBLE FIELD" }) }),
                  /* @__PURE__ */ jsxRuntimeExports.jsx("td", { className: "col-right tabular", children: fmtShortDateTime(d.last_scanned_ts) }),
                  /* @__PURE__ */ jsxRuntimeExports.jsx("td", { className: "col-right", children: /* @__PURE__ */ jsxRuntimeExports.jsx(
                    "button",
                    {
                      type: "button",
                      className: "btn btn-primary btn-sm",
                      onClick: () => handleRecycleDebris(coords),
                      children: "Recycle"
                    }
                  ) })
                ] }, `${coords}-${idx}`);
              }) })
            ] }) }) }),
            /* @__PURE__ */ jsxRuntimeExports.jsxs("div", { className: "intel-pane", id: "rec-pane-emails", hidden: recTab !== "emails", children: [
              /* @__PURE__ */ jsxRuntimeExports.jsx("div", { className: "email-filter-bar", children: /* @__PURE__ */ jsxRuntimeExports.jsx("div", { className: "filter-chips", role: "group", "aria-label": "Email category filters", children: EMAIL_CATS.map((c) => /* @__PURE__ */ jsxRuntimeExports.jsx(
                "button",
                {
                  type: "button",
                  className: `chip-btn${emailCat === c.key ? " active" : ""}`,
                  "data-email-cat": c.key,
                  onClick: () => handleSelectEmailCat(c.key),
                  children: c.label
                },
                c.key
              )) }) }),
              /* @__PURE__ */ jsxRuntimeExports.jsx("div", { className: "table-wrap table-scroll", children: /* @__PURE__ */ jsxRuntimeExports.jsxs("table", { children: [
                /* @__PURE__ */ jsxRuntimeExports.jsx("thead", { children: /* @__PURE__ */ jsxRuntimeExports.jsxs("tr", { children: [
                  /* @__PURE__ */ jsxRuntimeExports.jsx("th", { children: "Time" }),
                  /* @__PURE__ */ jsxRuntimeExports.jsx("th", { children: "Folder / Type" }),
                  /* @__PURE__ */ jsxRuntimeExports.jsx("th", { children: "Sender" }),
                  /* @__PURE__ */ jsxRuntimeExports.jsx("th", { children: "Subject" }),
                  /* @__PURE__ */ jsxRuntimeExports.jsx("th", { children: "Coords" }),
                  /* @__PURE__ */ jsxRuntimeExports.jsx("th", { children: "Message Body" })
                ] }) }),
                /* @__PURE__ */ jsxRuntimeExports.jsx("tbody", { id: "rec-emails-tbody", children: emails.length === 0 ? /* @__PURE__ */ jsxRuntimeExports.jsx("tr", { children: /* @__PURE__ */ jsxRuntimeExports.jsx("td", { colSpan: 6, className: "empty-cell", children: "No other emails or fleet notices archived yet." }) }) : emails.map((m, idx) => {
                  const rawTxt = String(m.body_text || "").trim();
                  const bodyTxt = rawTxt.length > 260 ? `${rawTxt.slice(0, 260)}...` : rawTxt || "—";
                  return /* @__PURE__ */ jsxRuntimeExports.jsxs("tr", { children: [
                    /* @__PURE__ */ jsxRuntimeExports.jsx("td", { className: "tabular", children: fmtShortDateTime(m.ts ?? m.ingested_ts, m.msg_date_text) }),
                    /* @__PURE__ */ jsxRuntimeExports.jsx("td", { className: "tabular", children: `${m.folder_type || "inbox"} · ${m.category || "general"}` }),
                    /* @__PURE__ */ jsxRuntimeExports.jsx("td", { children: String(m.sender || "System") }),
                    /* @__PURE__ */ jsxRuntimeExports.jsx("td", { children: String(m.subject || "—") }),
                    /* @__PURE__ */ jsxRuntimeExports.jsx("td", { className: "tabular", children: String(m.coords || "—") }),
                    /* @__PURE__ */ jsxRuntimeExports.jsx("td", { className: "msg-body-cell", children: bodyTxt })
                  ] }, m.msg_uid ?? m.id ?? idx);
                }) })
              ] }) })
            ] })
          ] })
        ]
      }
    );
  };
  const RankTrajectorySvg = ({ samples }) => {
    const w = 420;
    const h = 130;
    const padX = 14;
    const padY = 14;
    const vals = (Array.isArray(samples) ? samples : []).map((s) => finiteNum(s == null ? void 0 : s.estimated_points, null)).filter((v) => v !== null);
    let minV = 0;
    let maxV = 0;
    let rawSpan = 0;
    let span = 0.5;
    if (vals.length > 0) {
      minV = Math.min(...vals);
      maxV = Math.max(...vals);
      rawSpan = maxV - minV;
      span = Number.isFinite(rawSpan) && rawSpan >= 0.5 ? rawSpan : 0.5;
    }
    const toXY = (idx, val) => {
      const x = vals.length === 1 ? w / 2 : padX + idx / (vals.length - 1) * (w - padX * 2);
      const ratio = Number.isFinite(rawSpan) && rawSpan > 0 ? (val - minV) / span : 0;
      const rawY = h - padY - (Number.isFinite(ratio) ? ratio : 0) * (h - padY * 2);
      const y = Number.isFinite(rawY) ? rawY : h / 2;
      return [x.toFixed(1), y.toFixed(1)];
    };
    const ptsAttr = vals.length >= 2 ? vals.map((v, i) => toXY(i, v).join(",")).join(" ") : "";
    return /* @__PURE__ */ jsxRuntimeExports.jsxs(
      "svg",
      {
        id: "rnk-chart",
        viewBox: "0 0 420 130",
        role: "img",
        "aria-label": "Estimated empire points over recent observations",
        children: [
          [1, 2, 3].map((i) => {
            const y = Math.round(h / 4 * i);
            return /* @__PURE__ */ jsxRuntimeExports.jsx(
              "line",
              {
                x1: String(padX),
                x2: String(w - padX),
                y1: String(y),
                y2: String(y),
                stroke: "#243135",
                strokeDasharray: "3 3"
              },
              i
            );
          }),
          vals.length === 0 ? /* @__PURE__ */ jsxRuntimeExports.jsx("text", { x: String(w / 2), y: String(h / 2), textAnchor: "middle", fill: "#9eb3b8", fontSize: "12", children: "Collecting samples into SQLite (rank_snapshots)..." }) : /* @__PURE__ */ jsxRuntimeExports.jsxs(jsxRuntimeExports.Fragment, { children: [
            vals.length >= 2 && /* @__PURE__ */ jsxRuntimeExports.jsx("polyline", { fill: "none", stroke: "#16ec98", strokeWidth: "2", points: ptsAttr }),
            vals.map((v, i) => {
              const [cx, cy] = toXY(i, v);
              const isLast = i === vals.length - 1;
              return /* @__PURE__ */ jsxRuntimeExports.jsx(
                "circle",
                {
                  cx,
                  cy,
                  r: isLast ? "3.5" : "2",
                  fill: isLast ? "#e6eef0" : "#16ec98"
                },
                i
              );
            })
          ] })
        ]
      }
    );
  };
  function fmtSignedDeltaInt(val) {
    const rounded = Math.round(val);
    const clean = rounded === 0 ? 0 : rounded;
    return `${clean >= 0 ? "+" : ""}${fmtInt(clean)}`;
  }
  const RankingView = ({ hidden }) => {
    const { dashboard, activePage, apiOnline, apiCommand: apiCommand2, loadDashboard, showFeedback } = useConsole();
    const [localUniRows, setLocalUniRows] = reactExports.useState(null);
    const [syncingUniRes, setSyncingUniRes] = reactExports.useState(false);
    const isHidden = hidden ?? activePage !== "ranking";
    const rnk = (dashboard == null ? void 0 : dashboard.ranking) || null;
    reactExports.useEffect(() => {
      const dashRows = dashboard == null ? void 0 : dashboard.universe_research_max;
      if (!Array.isArray(dashRows) || dashRows.length === 0) return;
      setLocalUniRows((prev) => {
        if (!prev || prev.length === 0) return null;
        const prevMaxTs = Math.max(...prev.map((r) => finiteNum(r.updated_ts, 0)));
        const dashMaxTs = Math.max(...dashRows.map((r) => finiteNum(r.updated_ts, 0)));
        return dashMaxTs >= prevMaxTs ? null : prev;
      });
    }, [dashboard == null ? void 0 : dashboard.universe_research_max]);
    reactExports.useEffect(() => {
      if (activePage === "ranking" && apiOnline && !Array.isArray(dashboard == null ? void 0 : dashboard.universe_research_max) && localUniRows === null) {
        let cancelled = false;
        apiCommand2({
          command: "universe_research_max",
          sync: false
        }).then((res) => {
          if (!cancelled && Array.isArray(res == null ? void 0 : res.rows)) {
            setLocalUniRows(res.rows);
          }
        }).catch(() => {
        });
        return () => {
          cancelled = true;
        };
      }
    }, [activePage, apiCommand2, apiOnline, dashboard == null ? void 0 : dashboard.universe_research_max, localUniRows]);
    const handleSyncUniverseResearch = async () => {
      setSyncingUniRes(true);
      try {
        showFeedback("Syncing universe max research records...", "ok");
        const res = await apiCommand2({
          command: "universe_research_max",
          sync: true
        });
        if (Array.isArray(res == null ? void 0 : res.rows)) {
          setLocalUniRows(res.rows);
        }
        showFeedback(
          (res == null ? void 0 : res.message) || "Universe max research records synced.",
          "ok"
        );
        await loadDashboard({ refresh: false, force: true });
      } catch (err) {
        showFeedback(
          `Universe research sync failed: ${err.message}`,
          feedbackKindForError(err)
        );
      } finally {
        setSyncingUniRes(false);
      }
    };
    const cur = (rnk == null ? void 0 : rnk.current) || {};
    const az = cur.attack_zone || {};
    const rates = (rnk == null ? void 0 : rnk.rates) || {};
    const trend = (rnk == null ? void 0 : rnk.observed_trend) || {};
    const srvRank = finiteNum(cur.server_rank, null);
    const srvPts = finiteNum(cur.server_points, null);
    let serverPosTxt = "—";
    let estPtsTxt = "—";
    let azLabelTxt = "—";
    let azRangeTxt = "—";
    let rateHrTxt = "—";
    let resHrTxt = "—";
    let breakdownTxt = "—";
    let spiceValTxt = "—";
    if (rnk) {
      const srvRankTxt = cur.server_rank != null ? srvRank !== null ? `#${fmtInt(srvRank)}` : "#—" : "Rank pending";
      const srvPtsTxt = cur.server_points != null ? srvPts !== null ? `${fmtInt(srvPts)} pts` : "— pts" : "— pts";
      serverPosTxt = `${srvRankTxt} · ${srvPtsTxt}`;
      const estPts = finiteNum(cur.estimated_points, 0);
      const cleanEstPts = Math.abs(estPts) < 0.05 ? 0 : estPts;
      estPtsTxt = `~${cleanEstPts.toFixed(1)} est. score (bld/res/flt)`;
      azLabelTxt = String(az.label || "—");
      const shield = cur.newbie_protected ? `Shielded (under 10k · ${cur.newbie_eta_compound || "—"})` : "Open AZ";
      azRangeTxt = `${az.range_text || ""} · ${shield}`;
      const steadyRate = finiteNum(rates.pts_per_hr_steady, 0);
      const netResRate = finiteNum(rates.net_resources_per_hr, 0);
      const spdFactor = finiteNum(rates.speedup_factor, 1);
      const cleanSteady = Math.abs(steadyRate) < 5e-3 ? 0 : steadyRate;
      rateHrTxt = `${cleanSteady >= 0 ? "+" : ""}${cleanSteady.toFixed(2)} pts/hr`;
      resHrTxt = `${fmtInt(Math.round(netResRate))} res/hr · x${spdFactor}`;
      const estPtsRaw = finiteNum(cur.estimated_points, null);
      const bldPts = finiteNum(cur.buildings_points, null);
      const resPts = finiteNum(cur.research_points, null);
      let bPct = 0;
      let rPct = 0;
      if (estPtsRaw !== null && estPtsRaw > 0 && bldPts !== null && resPts !== null) {
        bPct = clampPct$1(Math.round(bldPts / estPtsRaw * 100));
        rPct = clampPct$1(Math.round(resPts / estPtsRaw * 100));
        if (bPct + rPct > 100) {
          rPct = Math.max(0, 100 - bPct);
        }
      }
      const fPct = Math.max(0, 100 - bPct - rPct);
      breakdownTxt = `Bld ${bPct}% · Res ${rPct}% · Flt ${fPct}%`;
      const spiceVal = finiteNum(cur.spice_value, 0);
      spiceValTxt = `${fmtInt(Math.round(spiceVal))} Spice Eq`;
    }
    const count = Math.max(0, Math.round(finiteNum(trend.sample_count, 0)));
    const deltaPts = finiteNum(trend.delta_points, null);
    const obsRate = finiteNum(trend.observed_pts_per_hr, null);
    const elapsedHrs = finiteNum(trend.elapsed_hours, null);
    const rankClimbed = finiteNum(trend.rank_climbed, null);
    let observedRateTxt = rnk ? "Collecting samples..." : "Trend collecting...";
    let observedSummaryTxt = rnk ? "Rate delta activates automatically after 2 distinct observations." : "Snapshots recorded in SQLite (rank_snapshots).";
    if (rnk && count >= 2 && deltaPts !== null) {
      const cleanDelta = deltaPts === 0 ? 0 : deltaPts;
      const deltaSign = cleanDelta >= 0 ? "+" : "";
      const cleanObsRate = obsRate !== null ? obsRate === 0 ? 0 : obsRate : null;
      const rateSign = cleanObsRate !== null && cleanObsRate >= 0 ? "+" : "";
      let rankStr = "";
      if (rankClimbed !== null) {
        const cleanRank = rankClimbed === 0 ? 0 : rankClimbed;
        if (cleanRank > 0) {
          rankStr = ` · Rank climbed +${fmtInt(cleanRank)}`;
        } else if (cleanRank < 0) {
          rankStr = ` · Rank dropped ${fmtInt(Math.abs(cleanRank))}`;
        } else {
          rankStr = " · Rank unchanged (0)";
        }
      }
      observedRateTxt = cleanObsRate !== null ? `${rateSign}${cleanObsRate} pts/hr observed` : `${deltaSign}${cleanDelta} pts`;
      observedSummaryTxt = `Window ${elapsedHrs !== null ? elapsedHrs : "—"}h: ${deltaSign}${cleanDelta} pts${rankStr}.`;
    }
    const horizons = Array.isArray(rnk == null ? void 0 : rnk.horizons) ? rnk.horizons : [];
    const milestones = Array.isArray(rnk == null ? void 0 : rnk.milestones) ? rnk.milestones : [];
    const uniRows = localUniRows ?? (Array.isArray(dashboard == null ? void 0 : dashboard.universe_research_max) ? dashboard.universe_research_max : []);
    return /* @__PURE__ */ jsxRuntimeExports.jsx(
      "section",
      {
        id: "view-ranking",
        className: "page-view",
        "aria-label": "Ranking evolution and forecast",
        hidden: isHidden,
        children: /* @__PURE__ */ jsxRuntimeExports.jsxs("article", { className: "hud-window page-fill", children: [
          /* @__PURE__ */ jsxRuntimeExports.jsxs("div", { className: "window-bar", children: [
            /* @__PURE__ */ jsxRuntimeExports.jsxs("div", { className: "window-title-group", children: [
              /* @__PURE__ */ jsxRuntimeExports.jsx("span", { className: "module-code tabular", children: "RNK" }),
              /* @__PURE__ */ jsxRuntimeExports.jsx("h2", { children: "Ranking Evolution, Attack Zone & Growth Forecast" })
            ] }),
            /* @__PURE__ */ jsxRuntimeExports.jsx("span", { className: "module-meta tabular", id: "rnk-trend-meta", children: `${count} sample${count === 1 ? "" : "s"}` })
          ] }),
          /* @__PURE__ */ jsxRuntimeExports.jsxs("div", { className: "ranking-pane", children: [
            /* @__PURE__ */ jsxRuntimeExports.jsxs("div", { className: "ranking-kpi-grid", children: [
              /* @__PURE__ */ jsxRuntimeExports.jsxs("div", { className: "kpi-card", children: [
                /* @__PURE__ */ jsxRuntimeExports.jsx("span", { className: "kpi-label tabular", children: "Server Rank & Score" }),
                /* @__PURE__ */ jsxRuntimeExports.jsx("strong", { className: "kpi-value tabular", id: "rnk-server-pos", children: serverPosTxt }),
                /* @__PURE__ */ jsxRuntimeExports.jsx("span", { className: "kpi-sub tabular", id: "rnk-est-pts", children: estPtsTxt })
              ] }),
              /* @__PURE__ */ jsxRuntimeExports.jsxs("div", { className: "kpi-card", children: [
                /* @__PURE__ */ jsxRuntimeExports.jsx("span", { className: "kpi-label tabular", children: "Attack Zone Tier" }),
                /* @__PURE__ */ jsxRuntimeExports.jsx("strong", { className: "kpi-value tabular", id: "rnk-az-label", children: azLabelTxt }),
                /* @__PURE__ */ jsxRuntimeExports.jsx("span", { className: "kpi-sub tabular", id: "rnk-az-range", children: azRangeTxt })
              ] }),
              /* @__PURE__ */ jsxRuntimeExports.jsxs("div", { className: "kpi-card", children: [
                /* @__PURE__ */ jsxRuntimeExports.jsx("span", { className: "kpi-label tabular", children: "Hourly Growth Pace" }),
                /* @__PURE__ */ jsxRuntimeExports.jsx("strong", { className: "kpi-value tabular", id: "rnk-rate-hr", children: rateHrTxt }),
                /* @__PURE__ */ jsxRuntimeExports.jsx("span", { className: "kpi-sub tabular", id: "rnk-res-hr", children: resHrTxt })
              ] }),
              /* @__PURE__ */ jsxRuntimeExports.jsxs("div", { className: "kpi-card", children: [
                /* @__PURE__ */ jsxRuntimeExports.jsx("span", { className: "kpi-label tabular", children: "Empire Breakdown" }),
                /* @__PURE__ */ jsxRuntimeExports.jsx("strong", { className: "kpi-value tabular", id: "rnk-breakdown", children: breakdownTxt }),
                /* @__PURE__ */ jsxRuntimeExports.jsx("span", { className: "kpi-sub tabular", id: "rnk-spice-val", children: spiceValTxt })
              ] })
            ] }),
            /* @__PURE__ */ jsxRuntimeExports.jsxs("div", { className: "ranking-grid", children: [
              /* @__PURE__ */ jsxRuntimeExports.jsxs("div", { className: "ranking-chart-card", children: [
                /* @__PURE__ */ jsxRuntimeExports.jsxs("div", { className: "card-subhead", children: [
                  /* @__PURE__ */ jsxRuntimeExports.jsx("h3", { children: "Trajectory Curve" }),
                  /* @__PURE__ */ jsxRuntimeExports.jsx("span", { className: "tabular", id: "rnk-observed-rate", children: observedRateTxt })
                ] }),
                /* @__PURE__ */ jsxRuntimeExports.jsx(RankTrajectorySvg, { samples: trend.samples || [] }),
                /* @__PURE__ */ jsxRuntimeExports.jsx("p", { className: "action-sub tabular", id: "rnk-observed-summary", children: observedSummaryTxt })
              ] }),
              /* @__PURE__ */ jsxRuntimeExports.jsxs("div", { className: "ranking-tables-stack", children: [
                /* @__PURE__ */ jsxRuntimeExports.jsx("div", { className: "table-wrap", children: /* @__PURE__ */ jsxRuntimeExports.jsxs("table", { children: [
                  /* @__PURE__ */ jsxRuntimeExports.jsx("thead", { children: /* @__PURE__ */ jsxRuntimeExports.jsxs("tr", { children: [
                    /* @__PURE__ */ jsxRuntimeExports.jsx("th", { children: "Horizon" }),
                    /* @__PURE__ */ jsxRuntimeExports.jsx("th", { className: "col-right", children: "Steady" }),
                    /* @__PURE__ */ jsxRuntimeExports.jsx("th", { className: "col-right", children: "AZ" }),
                    /* @__PURE__ */ jsxRuntimeExports.jsx("th", { className: "col-right", children: "Reinvested" }),
                    /* @__PURE__ */ jsxRuntimeExports.jsx("th", { className: "col-right", children: "AZ" })
                  ] }) }),
                  /* @__PURE__ */ jsxRuntimeExports.jsx("tbody", { id: "rnk-horizons-tbody", children: horizons.length === 0 ? /* @__PURE__ */ jsxRuntimeExports.jsx("tr", { children: /* @__PURE__ */ jsxRuntimeExports.jsx("td", { colSpan: 5, className: "empty-cell", children: "Waiting for ranking forecast..." }) }) : horizons.map((row, idx) => {
                    const sp = finiteNum(row.steady_points, null);
                    const sd = finiteNum(row.steady_delta, null);
                    const cp = finiteNum(row.compound_points, null);
                    const cd = finiteNum(row.compound_delta, null);
                    return /* @__PURE__ */ jsxRuntimeExports.jsxs("tr", { children: [
                      /* @__PURE__ */ jsxRuntimeExports.jsx("td", { children: String(row.label || "—") }),
                      /* @__PURE__ */ jsxRuntimeExports.jsx("td", { className: "col-right tabular", children: sp !== null && sd !== null ? `${fmtInt(Math.round(sp))} (${fmtSignedDeltaInt(sd)})` : "—" }),
                      /* @__PURE__ */ jsxRuntimeExports.jsx("td", { className: "col-right tabular", children: String(row.steady_az || "—") }),
                      /* @__PURE__ */ jsxRuntimeExports.jsx("td", { className: "col-right tabular", children: cp !== null && cd !== null ? `${fmtInt(Math.round(cp))} (${fmtSignedDeltaInt(cd)})` : "—" }),
                      /* @__PURE__ */ jsxRuntimeExports.jsx("td", { className: "col-right tabular", children: String(row.compound_az || "—") })
                    ] }, row.label || idx);
                  }) })
                ] }) }),
                /* @__PURE__ */ jsxRuntimeExports.jsx("div", { className: "table-wrap", children: /* @__PURE__ */ jsxRuntimeExports.jsxs("table", { children: [
                  /* @__PURE__ */ jsxRuntimeExports.jsx("thead", { children: /* @__PURE__ */ jsxRuntimeExports.jsxs("tr", { children: [
                    /* @__PURE__ */ jsxRuntimeExports.jsx("th", { children: "Next Milestone" }),
                    /* @__PURE__ */ jsxRuntimeExports.jsx("th", { className: "col-right", children: "Remaining" }),
                    /* @__PURE__ */ jsxRuntimeExports.jsx("th", { className: "col-right", children: "Steady ETA" }),
                    /* @__PURE__ */ jsxRuntimeExports.jsx("th", { className: "col-right", children: "Compound ETA" })
                  ] }) }),
                  /* @__PURE__ */ jsxRuntimeExports.jsx("tbody", { id: "rnk-milestones-tbody", children: milestones.length === 0 ? /* @__PURE__ */ jsxRuntimeExports.jsx("tr", { children: /* @__PURE__ */ jsxRuntimeExports.jsx("td", { colSpan: 4, className: "empty-cell", children: "Calculating milestones..." }) }) : milestones.map((m, idx) => {
                    const tgtPts = finiteNum(m.target_points, null);
                    const missPts = finiteNum(m.missing_points, null);
                    return /* @__PURE__ */ jsxRuntimeExports.jsxs("tr", { children: [
                      /* @__PURE__ */ jsxRuntimeExports.jsx("td", { children: `${m.label} (${tgtPts !== null ? fmtInt(tgtPts) : "—"})` }),
                      /* @__PURE__ */ jsxRuntimeExports.jsx("td", { className: "col-right tabular", children: missPts !== null ? `+${fmtInt(Math.round(missPts))}` : "—" }),
                      /* @__PURE__ */ jsxRuntimeExports.jsx("td", { className: "col-right tabular", children: String(m.steady_eta || "—") }),
                      /* @__PURE__ */ jsxRuntimeExports.jsx("td", { className: "col-right tabular", children: String(m.compound_eta || "—") })
                    ] }, m.label || idx);
                  }) })
                ] }) })
              ] })
            ] }),
            /* @__PURE__ */ jsxRuntimeExports.jsxs("div", { className: "ranking-chart-card", children: [
              /* @__PURE__ */ jsxRuntimeExports.jsxs("div", { className: "card-subhead", children: [
                /* @__PURE__ */ jsxRuntimeExports.jsx("h3", { children: "Universe Max Research Levels" }),
                /* @__PURE__ */ jsxRuntimeExports.jsx(
                  "button",
                  {
                    type: "button",
                    className: "btn btn-secondary btn-sm",
                    id: "btn-sync-universe-research",
                    onClick: handleSyncUniverseResearch,
                    disabled: syncingUniRes,
                    children: syncingUniRes ? "Syncing..." : "Sync Records"
                  }
                )
              ] }),
              /* @__PURE__ */ jsxRuntimeExports.jsx("div", { className: "table-wrap table-scroll", children: /* @__PURE__ */ jsxRuntimeExports.jsxs("table", { id: "universe-research-max-table", children: [
                /* @__PURE__ */ jsxRuntimeExports.jsx("thead", { children: /* @__PURE__ */ jsxRuntimeExports.jsxs("tr", { children: [
                  /* @__PURE__ */ jsxRuntimeExports.jsx("th", { children: "Research" }),
                  /* @__PURE__ */ jsxRuntimeExports.jsx("th", { className: "col-right", children: "Your Level" }),
                  /* @__PURE__ */ jsxRuntimeExports.jsx("th", { className: "col-right", children: "Universe Max" }),
                  /* @__PURE__ */ jsxRuntimeExports.jsx("th", { className: "col-right", children: "Delta / Gap" }),
                  /* @__PURE__ */ jsxRuntimeExports.jsx("th", { children: "Record Holder" }),
                  /* @__PURE__ */ jsxRuntimeExports.jsx("th", { className: "col-right", children: "Catalog Cap" })
                ] }) }),
                /* @__PURE__ */ jsxRuntimeExports.jsx("tbody", { id: "universe-research-max-tbody", children: uniRows.length === 0 ? /* @__PURE__ */ jsxRuntimeExports.jsx("tr", { children: /* @__PURE__ */ jsxRuntimeExports.jsx("td", { colSpan: 6, className: "empty-cell", children: "Click Sync Records to load universe max research levels." }) }) : uniRows.map((r) => {
                  const rKey = String(r.key || r.research_key || `r${r.research_id}`);
                  const ownLvl = Math.max(0, Math.round(finiteNum(r.own_level, 0)));
                  const uniMax = Math.max(0, Math.round(finiteNum(r.universe_max_level, 0)));
                  const gap = Math.max(
                    0,
                    Math.round(finiteNum(r.gap_to_max, Math.max(0, uniMax - ownLvl)))
                  );
                  const catCap = finiteNum(r.catalog_max_level, null);
                  const holderTxt = r.holder ? r.holder_coords ? `${r.holder} [${r.holder_coords}]` : String(r.holder) : uniMax > 0 && ownLvl >= uniMax ? "You" : "—";
                  return /* @__PURE__ */ jsxRuntimeExports.jsxs("tr", { children: [
                    /* @__PURE__ */ jsxRuntimeExports.jsxs("td", { children: [
                      `${r.name} `,
                      /* @__PURE__ */ jsxRuntimeExports.jsx("span", { className: "action-sub tabular", children: `(${rKey})` })
                    ] }),
                    /* @__PURE__ */ jsxRuntimeExports.jsx("td", { className: "col-right tabular", children: `L${fmtInt(ownLvl)}` }),
                    /* @__PURE__ */ jsxRuntimeExports.jsx("td", { className: "col-right tabular", children: `L${fmtInt(uniMax)}` }),
                    /* @__PURE__ */ jsxRuntimeExports.jsx("td", { className: "col-right tabular", children: gap <= 0 && uniMax > 0 ? /* @__PURE__ */ jsxRuntimeExports.jsx("span", { className: "status-word", children: "LEADER (0)" }) : gap > 0 ? /* @__PURE__ */ jsxRuntimeExports.jsx("span", { className: "status-word is-warn", children: `-${fmtInt(gap)} lvls` }) : "0" }),
                    /* @__PURE__ */ jsxRuntimeExports.jsx("td", { children: holderTxt }),
                    /* @__PURE__ */ jsxRuntimeExports.jsx("td", { className: "col-right tabular", children: catCap !== null && catCap > 0 ? `L${fmtInt(catCap)}` : "Uncapped" })
                  ] }, rKey);
                }) })
              ] }) })
            ] })
          ] })
        ] })
      }
    );
  };
  const CONFIG_GROUPS = [
    {
      id: "doctrine",
      headingId: "cfg-grp-doctrine",
      title: "Planner Engine & Empire Doctrine",
      subtitle: "Core planning engine selection, playstyle doctrine weighting, multi-planet scheduling scope, and colony expansion field thresholds.",
      items: [
        {
          key: "planner_mode",
          id: "cfg-planner_mode",
          title: "Planner Mode",
          boundsLabel: "default: priority · priority | ga_nn",
          description: "Selects between the deterministic rule-based P0–P3 heuristic pipeline (priority) and evolutionary chromosome search blended with the 46-feature value network (ga_nn). Saved immediately to SQLite so both Game API and Commander ticks share the active planner.",
          control: "select",
          defaultValue: "priority",
          options: [
            { value: "priority", label: "priority — Rule-Based Heuristic" },
            { value: "ga_nn", label: "ga_nn — GA + Neural Network" }
          ]
        },
        {
          key: "playstyle",
          id: "cfg-playstyle",
          title: "Playstyle Doctrine",
          boundsLabel: "default: fleeter · fleeter | hybrid | miner",
          description: "Weights P2 research ordering and shipyard hull quotas toward offensive warships and combat/drive technologies (fleeter), compound mine ROI payback (miner), or a balanced empire progression (hybrid). Applies whenever research or shipyard automation is enabled.",
          control: "select",
          defaultValue: "fleeter",
          options: [
            { value: "fleeter", label: "fleeter — Combat & Techs" },
            { value: "hybrid", label: "hybrid — Balanced" },
            { value: "miner", label: "miner — Economy ROI" }
          ]
        },
        {
          key: "allow_multi_planet",
          id: "cfg-allow_multi_planet",
          title: "Multi-Planet Empire Planning",
          boundsLabel: "default: true · boolean",
          description: "Evaluates construction queues, energy balance, warehouse caps, and fleet dispatch across every owned colony and Asteroid instead of restricting planning to the currently active planet. Respects SpaceInvasion Classic slot rules for normal planets, solar-only Asteroids (1 build slot), and isolated [RK] raid colonies.",
          control: "checkbox",
          defaultValue: true
        },
        {
          key: "max_planets",
          id: "cfg-max_planets",
          title: "Max Empire Planets Cap",
          boundsLabel: "default: 11 · 1–50 planets",
          description: "Caps total normal planets (owned colonies plus outbound COLONIZE flights) before halting autonomous Colony ship (s8) dispatches; SpaceInvasion Classic permits 1 homeworld plus up to 10 normal colonies (11 total, excluding Asteroids and [RK] raid colonies). Accepts integers from 1 to 50.",
          control: "number",
          defaultValue: 11,
          min: 1,
          max: 50,
          step: 1
        },
        {
          key: "min_colony_fields",
          id: "cfg-min_colony_fields",
          title: "Minimum Colony Fields Target",
          boundsLabel: "default: 300 · 50–600 fields",
          description: "Minimum buildable planet size required by the colony grader and P2 COLONIZE selector, favoring Prime Mid-Orbit positions 7–12 capable of rolling 300+ fields so late-game mines and Fusion plants never stall on slot exhaustion. Accepts integers from 50 to 600 fields.",
          control: "number",
          defaultValue: 300,
          min: 50,
          max: 600,
          step: 10
        },
        {
          key: "min_free_fields_warning",
          id: "cfg-min_free_fields_warning",
          title: "Low Free Fields Advisory Threshold",
          boundsLabel: "default: 20 · 1–1000 fields",
          description: "Raises a P3 non-executable colony space advisory (executable_now=False) and steers power upgrades toward compact Fusion plant levels once remaining buildable slots (fields_max - fields_used) drop at or below this buffer. Accepts integers from 1 to 1000 fields.",
          control: "number",
          defaultValue: 20,
          min: 1,
          max: 1e3,
          step: 1
        }
      ]
    },
    {
      id: "economy",
      headingId: "cfg-grp-economy",
      title: "Economy, Power & Storage Automation",
      subtitle: "Blast furnace (b2) 2:1 Pig iron throttling, planetary power generation policy, warehouse overflow guards, research/shipyard queues, and free IDT spins.",
      items: [
        {
          key: "auto_throttle_blast_furnace",
          id: "cfg-auto_throttle_blast_furnace",
          title: "Auto-Throttle Blast Furnace (b2)",
          boundsLabel: "default: true · boolean",
          description: "Automatically throttles Blast furnace (b2) to 0% via P1 THROTTLE_BUILDING when 2:1 Pig iron smelting starves frontier Pig iron upgrades or when Metal storage is 98%+ full while Pig iron storage has headroom, then restores 100% when Metal is needed. In SpaceInvasion Classic, b2 consumes 2 Pig iron per 1 Metal and is not boosted by Mining technology (r1).",
          control: "checkbox",
          defaultValue: true
        },
        {
          key: "blast_pig_iron_max_share",
          id: "cfg-blast_pig_iron_max_share",
          title: "Max Blast Furnace Pig Iron Share",
          boundsLabel: "default: 0.70 · 0.10–1.00",
          description: "Maximum share of gross Pig iron mine (b1) hourly output that Blast furnace (b2) may consume through its 2:1 smelting ratio before the planner holds further b2 levels or pauses smelting to preserve positive net Pig iron income. Accepts float ratios from 0.10 to 1.00.",
          control: "number",
          defaultValue: 0.7,
          min: 0.1,
          max: 1,
          step: 0.05
        },
        {
          key: "merchant_mode",
          id: "cfg-merchant_mode",
          title: "Merchant Exchange Mode (Skip b2)",
          boundsLabel: "default: false · boolean",
          description: "Evaluates mine payback ROI using NPC Merchant trade ratios (6 : 2.5 : 3 : 1 Pig iron / Metal / Kryptonite / Spice) instead of standard player ratios (4 : 2 : 2 : 1) and skips Blast furnace (b2) construction when Metal is sourced via trader swaps. Boolean toggle.",
          control: "checkbox",
          defaultValue: false
        },
        {
          key: "forbid_planetary_solar",
          id: "cfg-forbid_planetary_solar",
          title: "Forbid Solar Power on Normal Planets",
          boundsLabel: "default: false · boolean",
          description: "Forbids Thermosolar plant (b6) and combat-destructible orbital Solar cells (s9) on normal planets—relying exclusively on Fusion plant (b5) scaled by Energy technology (r2) and scheduling P1 step-down DEMOLISH_BUILDING of existing b6 levels when spare energy covers the loss. Asteroids remain exempt because SpaceInvasion Classic restricts Asteroids to solar power only.",
          control: "checkbox",
          defaultValue: false
        },
        {
          key: "hot_planet_solar_temp_threshold",
          id: "cfg-hot_planet_solar_temp_threshold",
          title: "Hot Planet Solar Temperature Threshold (°C)",
          boundsLabel: "default: 40 · -273 to 1000 °C",
          description: "Minimum colony temperature in °C required before emergency power recovery considers orbital Solar cells (s9) on tight-field normal planets when forbid_planetary_solar is disabled; hot inner-orbit slots boost solar yield whereas cold outer slots boost Spice mine (b4) output. Accepts integers from -273 to 1000 °C.",
          control: "number",
          defaultValue: 40,
          min: -273,
          max: 1e3,
          step: 5
        },
        {
          key: "storage_warn_ratio",
          id: "cfg-storage_warn_ratio",
          title: "Warehouse Overflow Warning Ratio",
          boundsLabel: "default: 0.85 · 0.10–0.99",
          description: "Schedules P1 warehouse upgrades (b11–b14: Pig iron, Metal, Kryptonite, or Spice warehouse) as soon as stored stock divided by warehouse capacity reaches this threshold, preventing SpaceInvasion mines from halting extraction at 100% cap. Accepts float ratios from 0.10 to 0.99.",
          control: "number",
          defaultValue: 0.85,
          min: 0.1,
          max: 0.99,
          step: 0.05
        },
        {
          key: "allow_research",
          id: "cfg-allow_research",
          title: "Autonomous Research Queue",
          boundsLabel: "default: true · boolean",
          description: "Queues P2 account-wide RESEARCH upgrades (r1–r15) from the colony with the highest Research lab (b8) level whenever the global research slot is idle; research duration scales with (b8 + 1) * speedup and is unaffected by Microsystem (b17). Boolean toggle.",
          control: "checkbox",
          defaultValue: true
        },
        {
          key: "allow_shipyard",
          id: "cfg-allow_shipyard",
          title: "Autonomous Shipyard Construction",
          boundsLabel: "default: true · boolean",
          description: "Enables P2 BUILD_SHIP orders at Weapons factory (b9) to maintain configured shipyard_targets quotas (Spy probe s10, Transports, Recycler s11, Colony ship s8, and combat hulls), where per-unit build time divides by b9 * 2^b17 * speedup. Boolean toggle.",
          control: "checkbox",
          defaultValue: true
        },
        {
          key: "allow_transmitter",
          id: "cfg-allow_transmitter",
          title: "Daily Free Interdimensional Transmitter Spin",
          boundsLabel: "default: false · boolean",
          description: "Schedules P2 ACTIVATE_TRANSMITTER once per Europe/Berlin server day to claim the free daily Interdimensional Transmitter spin, strictly guarded by _freeSpins >= 1 and idtSrc == 1 so zero paid Urplasma is ever spent. Boolean toggle.",
          control: "checkbox",
          defaultValue: false
        }
      ]
    },
    {
      id: "galaxy",
      headingId: "cfg-grp-galaxy",
      title: "Galaxy Scanner & Sweep Pacing",
      subtitle: "Spiral solar-system recon, the smart 2-phase scanner (exploratory 1–400 coverage, then a weighted refresh by proximity and attack history), multi-page 2D Galaxy si.navigate sweeps, delay and jitter between clicks, and free-slot colonization.",
      items: [
        {
          key: "allow_galaxy_scan",
          id: "cfg-allow_galaxy_scan",
          title: "Autonomous Galaxy Scanner & Background Sweep",
          boundsLabel: "default: false · boolean",
          description: "Enables P2 outward spiral SCAN_GALAXY system recon (1800s per-system cooldown) and background randomized 2D Galaxy sweeps that index yellow inactive farms, Spacia NPC planets, orbital rubble fields, and free colony slots in SQLite. Boolean toggle.",
          control: "checkbox",
          defaultValue: false
        },
        {
          key: "galaxy_sweep_pages",
          id: "cfg-galaxy_sweep_pages",
          title: "Galaxy Sweep Pages per Cycle",
          boundsLabel: "default: 100 · 1–400 pages",
          description: "Number of solar system pages (gal:sun) visited per randomized 2D Galaxy sweep cycle (and default page count for the Sweep 100 Pages action button) to keep sector intelligence fresh across neighboring systems. Accepts integers from 1 to 400 pages.",
          control: "number",
          defaultValue: 100,
          min: 1,
          max: 400,
          step: 10
        },
        {
          key: "galaxy_sweep_interval_seconds",
          id: "cfg-galaxy_sweep_interval_seconds",
          title: "Max Background Galaxy Sweep Interval (s)",
          boundsLabel: "default: 270 · 30–86400 s",
          description: "Maximum interval in seconds between automatic background multi-page Galaxy sweep runs when allow_galaxy_scan is enabled, jittered so sector recon refreshes regularly without starving build or fleet ticks. Accepts integers from 30 to 86400 seconds.",
          control: "number",
          defaultValue: 270,
          min: 30,
          max: 86400,
          step: 15
        },
        {
          key: "galaxy_scan_click_delay_seconds",
          id: "cfg-galaxy_scan_click_delay_seconds",
          title: "Time Between Galaxy Scan Clicks (s)",
          boundsLabel: "default: 1.5 · 0.2–60.0 s",
          description: "Base delay in seconds waited between consecutive step_galaxy_system / si.navigate page clicks during multi-page Galaxy sweeps to stay safely under SpaceInvasion's 30-page-view rate-limit cooldown. Accepts floats from 0.2 to 60.0 seconds.",
          control: "number",
          defaultValue: 1.5,
          min: 0.2,
          max: 60,
          step: 0.1
        },
        {
          key: "galaxy_scan_click_jitter_seconds",
          id: "cfg-galaxy_scan_click_jitter_seconds",
          title: "Random Jitter Between Galaxy Scan Pages (s)",
          boundsLabel: "default: 0.6 · 0.0–30.0 s",
          description: "Uniform random +/-jitter offset in seconds added around galaxy_scan_click_delay_seconds on each Galaxy sweep page step (floored at 0.2s total wait) so page navigation cadence stays organic rather than fixed-interval. Accepts floats from 0.0 to 30.0 seconds.",
          control: "number",
          defaultValue: 0.6,
          min: 0,
          max: 30,
          step: 0.1
        },
        {
          key: "galaxy_scan_explore_first",
          id: "cfg-galaxy_scan_explore_first",
          title: "Smart Scan Phase 1: Explore Unscanned Systems First",
          boundsLabel: "default: true · boolean",
          description: "Phase 1 (exploratory): Galaxy sweeps and SCAN_GALAXY visit every never-scanned solar system (1–400) in each galaxy where you own a non-raid colony before they revisit any system. Systems are ordered outward from your nearest colony so si.navigate steps stay contiguous. Once coverage reaches 100% (explored_systems == total_systems), or right away when this is off, sweeps switch to Phase 2: a weighted refresh that ranks sectors by proximity, attack history, and staleness. Boolean toggle.",
          control: "checkbox",
          defaultValue: true
        },
        {
          key: "galaxy_scan_near_weight",
          id: "cfg-galaxy_scan_near_weight",
          title: "Smart Scan Phase 2: Near-Sector Proximity Weight",
          boundsLabel: "default: 2.0 · 0.0–20.0",
          description: "Phase 2 (targeted_refresh) multiplier on the proximity score of each (galaxy, system) sector. Proximity uses the circular system distance to your closest non-raid colony, min(|s − own_s|, 400 − |s − own_s|), with a penalty for other galaxies. Higher values refresh nearby sectors (cheap raids, short Spy probe flights) more often; 0.0 ignores distance. Accepts floats from 0.0 to 20.0.",
          control: "number",
          defaultValue: 2,
          min: 0,
          max: 20,
          step: 0.5
        },
        {
          key: "galaxy_scan_attack_weight",
          id: "cfg-galaxy_scan_attack_weight",
          title: "Smart Scan Phase 2: Frequent-Attack Player & Sector Weight",
          boundsLabel: "default: 3.0 · 0.0–20.0",
          description: "Phase 2 (targeted_refresh) multiplier on attack-history affinity, read from SQLite raid_history. It combines direct raids and loot in a GG:SSS sector with per-owner frequency: every sector where a player you attack often owns a planet gets boosted. Debris, yellow inactive, Spacia, and 300+ free-slot value add a secondary score, and the total is multiplied by how stale the scan is. Higher values keep frequent farm targets fresh; 0.0 ignores attack history. Accepts floats from 0.0 to 20.0.",
          control: "number",
          defaultValue: 3,
          min: 0,
          max: 20,
          step: 0.5
        },
        {
          key: "allow_colonize",
          id: "cfg-allow_colonize",
          title: "Autonomous Free-Slot Colonization",
          boundsLabel: "default: false · boolean",
          description: "Probes non-discarded free slots (Prime Mid-Orbit 7–12 first) until qualified 300+-field candidates cover max_planets minus allocated colonies, then launches stationed Colony ships (s8) on verified 300+-field candidates when 1 + b10 fleet slots permit. Boolean toggle.",
          control: "checkbox",
          defaultValue: false
        }
      ]
    },
    {
      id: "combat",
      headingId: "cfg-grp-combat",
      title: "Espionage, Raiding & Fleet Safety",
      subtitle: "Reserved spy probe flight seats, spy origin colony selection, zero-rapidfire 1:1 combat simulator gates, 3/24h Bash tracking, and P0 SAVE Escape flights.",
      items: [
        {
          key: "allow_raids",
          id: "cfg-allow_raids",
          title: "Autonomous Espionage & Inactive/Spacia Raids",
          boundsLabel: "default: false · boolean",
          description: "Enables closest-first P2 SPY scouting (s10 on inhabited planets and Spacia, refreshing intel older than 4h) and ATTACK plunder waves against yellow inactives and Spacia NPC planets (exempt from the 3/24h Bash cap unless server-blocked), bounded by 1 + b10 concurrent fleet slots. Boolean toggle.",
          control: "checkbox",
          defaultValue: false
        },
        {
          key: "reserved_spy_slots",
          id: "cfg-reserved_spy_slots",
          title: "Reserved Spy Fleet Slots",
          boundsLabel: "default: 1 · 0–10 slots",
          description: "Number of Fleet base (1 + b10) concurrent flight seats held exclusively for SPY probe missions whenever ready spy targets and stationed s10 probes exist, holding non-SPY ATTACK, RECYCLE, and COLONIZE dispatches at max_fleet_slots - eff_res so espionage never stalls behind slow raids. Accepts integers from 0 to 10.",
          control: "number",
          defaultValue: 1,
          min: 0,
          max: 10,
          step: 1
        },
        {
          key: "spy_dispatch_planet_id",
          id: "cfg-spy_dispatch_planet_id",
          title: "Designated Spy Dispatch Colony",
          boundsLabel: "default: 0 (Auto) · colony planet_id",
          description: "Locks all SPY probe launches to a specific owned non-raid colony planet_id (holding SPY and prioritizing s10 construction there if its probe stock hits zero), or set 0 to automatically select the active or closest colony carrying stationed Spy probes (s10). Accepts 0 (Auto) or an owned colony ID.",
          control: "planet-select",
          defaultValue: 0
        },
        {
          key: "allow_pvp_in_az",
          id: "cfg-allow_pvp_in_az",
          title: "Allow Active-Player PvP Raids Inside Attack Zone",
          boundsLabel: "default: false · boolean",
          description: "Permits P2 SPY and ATTACK missions against active (light_red) players within your SpaceInvasion Attack Zone (AZ) tier, strictly enforcing the 3 attacks per 24 hours Bash rule per target planet and 6-hour cooldowns on newbie, vacation, or out-of-attackzone targets. Boolean toggle.",
          control: "checkbox",
          defaultValue: false
        },
        {
          key: "combat_safety_margin",
          id: "cfg-combat_safety_margin",
          title: "1:1 Combat Simulator Safety Margin",
          boundsLabel: "default: 1.4 · 1.0–1000.0x",
          description: "Minimum attacker-to-defender effective power ratio required in SpaceInvasion Classic's zero-rapidfire 1:1 combat simulator (incorporating Weapons r11, Shield r12, and Defense r13 tech multipliers) before authorizing any ATTACK wave. Accepts floats from 1.0 to 1000.0.",
          control: "number",
          defaultValue: 1.4,
          min: 1,
          max: 1e3,
          step: 0.1
        },
        {
          key: "raid_min_loot_spice_eq",
          id: "cfg-raid_min_loot_spice_eq",
          title: "Minimum Raid Plunder Threshold (Spice Eq)",
          boundsLabel: "default: 1500 · >= 0 SpEq",
          description: "Minimum expected 50% raid plunder converted into Spice-equivalent units (4 : 2 : 2 : 1 standard value) required before committing cargo hulls, Spice flight fuel, and a 1 + b10 fleet slot to an ATTACK mission. Accepts floats of 0 or higher.",
          control: "number",
          defaultValue: 1500,
          min: 0,
          step: 100
        },
        {
          key: "allow_fleetsave",
          id: "cfg-allow_fleetsave",
          title: "P0 Automatic Hostile Escape Flight (SAVE)",
          boundsLabel: "default: false · boolean",
          description: "Launches an immediate P0 ESCAPE_FLIGHT (SAVE mission) whenever an incoming hostile fleet targets a colony, evacuating stationed ships and packing cargo in Spice → Kryptonite → Metal → Pig iron priority order; SpaceInvasion Classic Escape flights are invisible to Asteroid Galaxy scanners. Boolean toggle.",
          control: "checkbox",
          defaultValue: false
        },
        {
          key: "fleet_save_fuel_reserve_spice",
          id: "cfg-fleet_save_fuel_reserve_spice",
          title: "Escape Flight Spice Fuel Reserve",
          boundsLabel: "default: 500 · >= 0 Spice",
          description: "Quantity of planetary Spice held back on the origin colony before loading ESCAPE_FLIGHT cargo holds so stationed fleets always retain sufficient propellant to launch the SAVE flight and follow-up missions. Accepts integers of 0 or higher.",
          control: "number",
          defaultValue: 500,
          min: 0,
          step: 50
        }
      ]
    },
    {
      id: "runtime",
      headingId: "cfg-grp-runtime",
      title: "Runtime Pacing & GA + Neural Network Optimizer",
      subtitle: "Autonomous Commander tick cadence, rolling hourly DOM mutation budget, and evolutionary ga_nn sequence search hyperparameters.",
      items: [
        {
          key: "action_batch_interval_seconds",
          id: "cfg-action_batch_interval_seconds",
          title: "Minimum Batch & Snapshot Refresh Interval (s)",
          boundsLabel: "default: 120 · 5–86400 s",
          description: "Minimum pause in seconds between autonomous Commander execution batches and full Chrome DOM snapshot refreshes when no active building or fleet timer completes sooner, pacing navigation under SpaceInvasion page-view limits. Accepts integers from 5 to 86400 seconds.",
          control: "number",
          defaultValue: 120,
          min: 5,
          max: 86400,
          step: 5
        },
        {
          key: "max_actions_per_hour",
          id: "cfg-max_actions_per_hour",
          title: "Max Live Actions per Rolling Hour",
          boundsLabel: "default: 60 · 1–100000 ops/hr",
          description: "Rolling 60-minute action cap limiting total live browser mutations (BUILD, RESEARCH, BUILD_SHIP, THROTTLE_BUILDING, and fleet dispatches) executed by GameApiBridge to prevent runaway click loops. Accepts integers from 1 to 100000 actions/hour.",
          control: "number",
          defaultValue: 60,
          min: 1,
          max: 1e5,
          step: 1
        },
        {
          key: "ga_population_size",
          id: "cfg-ga_population_size",
          title: "GA Population Size",
          boundsLabel: "default: 24 · 4–200 genomes",
          description: "Number of candidate action-sequence chromosomes maintained per evolutionary generation when planner_mode is ga_nn; larger populations explore broader mine, Fusion power, and tech build orders at higher CPU cost. Accepts integers from 4 to 200.",
          control: "number",
          defaultValue: 24,
          min: 4,
          max: 200,
          step: 1
        },
        {
          key: "ga_generations",
          id: "cfg-ga_generations",
          title: "GA Evolution Generations",
          boundsLabel: "default: 12 · 1–200 cycles",
          description: "Number of tournament-selection, crossover, and mutation cycles executed per ga_nn planning pass to converge on lower-wait, higher-SpiceEq multi-step build sequences. Accepts integers from 1 to 200.",
          control: "number",
          defaultValue: 12,
          min: 1,
          max: 200,
          step: 1
        },
        {
          key: "ga_horizon_steps",
          id: "cfg-ga_horizon_steps",
          title: "GA Rollout Horizon Steps",
          boundsLabel: "default: 6 · 1–24 steps",
          description: "Number of sequential actions simulated per chromosome through SpaceInvasion resource accumulation, Blast furnace 2:1 smelting, and Development center / Microsystem build-time formulas during ga_nn rollout scoring. Accepts integers from 1 to 24 steps.",
          control: "number",
          defaultValue: 6,
          min: 1,
          max: 24,
          step: 1
        },
        {
          key: "ga_mutation_rate",
          id: "cfg-ga_mutation_rate",
          title: "GA Genome Mutation Rate",
          boundsLabel: "default: 0.25 · 0.00–1.00",
          description: "Per-gene mutation probability applied during ga_nn offspring reproduction to diversify build-order search and escape local optima such as warehouse or power bottlenecks. Accepts floats from 0.00 to 1.00.",
          control: "number",
          defaultValue: 0.25,
          min: 0,
          max: 1,
          step: 0.05
        },
        {
          key: "ga_nn_weight",
          id: "cfg-ga_nn_weight",
          title: "46D Neural Value Blending Weight",
          boundsLabel: "default: 0.35 · 0.00–1.00",
          description: "Weight blending the 46-dimensional MLP value-network terminal state evaluation with deterministic Spice-equivalent production fitness during ga_nn scoring (0.0 pure rollout math, 1.0 full neural guidance). Accepts floats from 0.00 to 1.00.",
          control: "number",
          defaultValue: 0.35,
          min: 0,
          max: 1,
          step: 0.05
        },
        {
          key: "ga_seed",
          id: "cfg-ga_seed",
          title: "GA Deterministic PRNG Seed",
          boundsLabel: "default: Auto · 32-bit int or blank",
          description: "Optional integer seed pinning ga_nn random initialization, crossover, and mutation for deterministic, repeatable rollout plans; leave blank for fresh stochastic search on each pass. Accepts signed 32-bit integers or blank.",
          control: "text-seed",
          defaultValue: "",
          placeholder: "Auto"
        }
      ]
    }
  ];
  const CONFIG_BOOL_KEYS = CONFIG_GROUPS.flatMap(
    (g) => g.items.filter((i) => i.control === "checkbox").map((i) => i.key)
  );
  const CONFIG_NUMBER_DEFAULTS = Object.fromEntries(
    CONFIG_GROUPS.flatMap(
      (g) => g.items.filter((i) => i.control === "number").map((i) => [i.key, Number(i.defaultValue)])
    )
  );
  const useClientSyncEffect = typeof window !== "undefined" ? reactExports.useLayoutEffect : reactExports.useEffect;
  function fmtShortUpdated(tsSec) {
    const ts = safeFiniteNumber(tsSec, null);
    if (ts === null || ts <= 0) return "—";
    const d = new Date(ts * 1e3);
    if (Number.isNaN(d.getTime())) return "—";
    const mm = String(d.getMonth() + 1).padStart(2, "0");
    const dd = String(d.getDate()).padStart(2, "0");
    const hh = String(d.getHours()).padStart(2, "0");
    const mi = String(d.getMinutes()).padStart(2, "0");
    return `${mm}-${dd} ${hh}:${mi}`;
  }
  function buildFormStateFromConfig(cfg) {
    const out = {
      planner_mode: String((cfg == null ? void 0 : cfg.planner_mode) || "priority"),
      playstyle: String((cfg == null ? void 0 : cfg.playstyle) || "fleeter"),
      spy_dispatch_planet_id: String(
        Math.max(0, Math.round(safeFiniteNumber(cfg == null ? void 0 : cfg.spy_dispatch_planet_id, 0) ?? 0))
      ),
      ga_seed: (cfg == null ? void 0 : cfg.ga_seed) != null ? String(cfg.ga_seed) : ""
    };
    for (const bKey of CONFIG_BOOL_KEYS) {
      const defItem = CONFIG_GROUPS.flatMap((g) => g.items).find((i) => i.key === bKey);
      out[bKey] = cfg && bKey in cfg ? Boolean(cfg[bKey]) : Boolean(defItem == null ? void 0 : defItem.defaultValue);
    }
    for (const [nKey, defVal] of Object.entries(CONFIG_NUMBER_DEFAULTS)) {
      const v = cfg && cfg[nKey] != null ? cfg[nKey] : defVal;
      out[nKey] = String(v);
    }
    return out;
  }
  const ConfigView = () => {
    var _a, _b;
    const {
      dashboard,
      setDashboard,
      activePage,
      configHydrationTick,
      setConfigDirty,
      setConfigSaving,
      bumpDashboardSeq,
      beginGaCalc,
      endGaCalc,
      apiCommand: apiCommand2,
      loadDashboard,
      showFeedback
    } = useConsole();
    const [formValues, setFormValues] = reactExports.useState(
      () => buildFormStateFromConfig(dashboard == null ? void 0 : dashboard.config)
    );
    const [localResearchRows, setLocalResearchRows] = reactExports.useState(null);
    const hydrateFromConfig = reactExports.useCallback((cfg) => {
      if (!cfg) return;
      const next = buildFormStateFromConfig(cfg);
      setFormValues(next);
      if (typeof document !== "undefined") {
        for (const [k, v] of Object.entries(next)) {
          const node = document.getElementById(`cfg-${k}`);
          if (!node) continue;
          if (node instanceof HTMLInputElement && node.type === "checkbox") {
            node.checked = Boolean(v);
          } else {
            node.value = String(v ?? "");
          }
        }
      }
    }, []);
    reactExports.useEffect(() => {
      const gState = globalThis.state;
      if (!(gState == null ? void 0 : gState.configDirty) && !((gState == null ? void 0 : gState.configSaving) > 0) && (dashboard == null ? void 0 : dashboard.config)) {
        hydrateFromConfig(dashboard.config);
      }
    }, [configHydrationTick, dashboard == null ? void 0 : dashboard.config, hydrateFromConfig]);
    useClientSyncEffect(() => {
      globalThis.renderConfigForm = (cfg) => {
        reactDomExports.flushSync(() => hydrateFromConfig(cfg));
      };
    }, [hydrateFromConfig]);
    const markFieldChanged = (key, val) => {
      setConfigDirty(true);
      setFormValues((prev) => ({ ...prev, [key]: val }));
    };
    const nonRaidPlanets = (((_a = dashboard == null ? void 0 : dashboard.state) == null ? void 0 : _a.planets) || []).filter(
      (p) => String((p == null ? void 0 : p.planet_type) || "normal").toLowerCase() !== "raid"
    );
    const savedSpyPid = Math.max(
      0,
      Math.round(
        safeFiniteNumber(
          formValues.spy_dispatch_planet_id ?? ((_b = dashboard == null ? void 0 : dashboard.config) == null ? void 0 : _b.spy_dispatch_planet_id),
          0
        ) ?? 0
      )
    );
    const hasSavedPlanetOption = savedSpyPid === 0 || nonRaidPlanets.some((p) => Number(p.planet_id) === savedSpyPid);
    const onSaveConfig = async () => {
      const readEl = (id) => typeof document !== "undefined" ? document.getElementById(id) : null;
      const modeNode = readEl("cfg-planner_mode");
      const playNode = readEl("cfg-playstyle");
      const spyPidNode = readEl("cfg-spy_dispatch_planet_id");
      const plannerModeVal = modeNode ? modeNode.value : String(formValues.planner_mode || "priority");
      const playstyleVal = playNode ? playNode.value : String(formValues.playstyle || "fleeter");
      const rawSpyPid = spyPidNode ? spyPidNode.value : String(formValues.spy_dispatch_planet_id || "0");
      const syncedForm = {
        ...formValues,
        planner_mode: plannerModeVal,
        playstyle: playstyleVal,
        spy_dispatch_planet_id: String(
          Math.max(0, Math.round(safeFiniteNumber(rawSpyPid, 0) ?? 0))
        )
      };
      const payload = {
        planner_mode: plannerModeVal,
        playstyle: playstyleVal,
        spy_dispatch_planet_id: Math.max(0, Math.round(safeFiniteNumber(rawSpyPid, 0) ?? 0))
      };
      for (const bKey of CONFIG_BOOL_KEYS) {
        const box = readEl(`cfg-${bKey}`);
        const checkedVal = box ? Boolean(box.checked) : Boolean(formValues[bKey]);
        payload[bKey] = checkedVal;
        syncedForm[bKey] = checkedVal;
      }
      for (const numField of Object.keys(CONFIG_NUMBER_DEFAULTS)) {
        const inp = readEl(`cfg-${numField}`);
        const raw = inp ? String(inp.value ?? "").trim() : String(formValues[numField] ?? "").trim();
        syncedForm[numField] = inp ? String(inp.value ?? "") : String(formValues[numField] ?? "");
        if (raw !== "") {
          const parsed = safeFiniteNumber(raw, null);
          payload[numField] = parsed !== null ? parsed : raw;
        }
      }
      const seedInp = readEl("cfg-ga_seed");
      const rawSeed = seedInp ? String(seedInp.value ?? "").trim() : String(formValues.ga_seed ?? "").trim();
      syncedForm.ga_seed = seedInp ? String(seedInp.value ?? "") : String(formValues.ga_seed ?? "");
      if (rawSeed === "") {
        payload.ga_seed = null;
      } else if (/^-?\d+$/.test(rawSeed)) {
        const parsedSeed = Number(rawSeed);
        payload.ga_seed = Number.isSafeInteger(parsedSeed) ? parsedSeed : rawSeed;
      } else {
        payload.ga_seed = rawSeed;
      }
      setFormValues(syncedForm);
      const isGa = String(plannerModeVal).toLowerCase() === "ga_nn";
      if (isGa) beginGaCalc();
      bumpDashboardSeq();
      const gState = globalThis.state;
      if (gState) {
        gState.configSaving = (gState.configSaving || 0) + 1;
      }
      setConfigSaving((c) => c + 1);
      try {
        const res = await apiCommand2({ command: "update_config", config: payload });
        bumpDashboardSeq();
        setConfigDirty(false);
        if (res.config && typeof res.config === "object") {
          const curDash = (gState == null ? void 0 : gState.dashboard) || dashboard;
          const nextDash = curDash ? {
            ...curDash,
            config: res.config,
            planner_mode: res.config.planner_mode || curDash.planner_mode
          } : {
            config: res.config,
            planner_mode: res.config.planner_mode || "priority"
          };
          if (gState) {
            gState.dashboard = nextDash;
          }
          setDashboard(nextDash);
          hydrateFromConfig(res.config);
        }
        showFeedback("Saved config.yaml — Commander & API updated.", "ok");
        await loadDashboard({ refresh: false, preserveConfig: false, force: true });
      } catch (err) {
        setConfigDirty(true);
        showFeedback(`Failed saving config: ${err.message}`, feedbackKindForError(err));
      } finally {
        if (gState) {
          gState.configSaving = Math.max(0, (gState.configSaving || 0) - 1);
        }
        setConfigSaving((c) => Math.max(0, c - 1));
        if (isGa) endGaCalc();
      }
    };
    const onClaimTransmitter = async () => {
      try {
        showFeedback("Checking daily free Interdimensional Transmitter spin...", "info");
        const res = await apiCommand2({ command: "activate_transmitter", dry_run: false });
        showFeedback(
          res.message || "Interdimensional Transmitter check completed.",
          res.executed ? "ok" : "info"
        );
        await loadDashboard({ refresh: false, force: true });
      } catch (err) {
        showFeedback(`Transmitter check failed: ${err.message}`, feedbackKindForError(err));
      }
    };
    const onGalaxySweep = async () => {
      const pagesInp = typeof document !== "undefined" ? document.getElementById("cfg-galaxy_sweep_pages") : null;
      const pagesVal = safeFiniteNumber(pagesInp ? pagesInp.value : formValues.galaxy_sweep_pages, 100) || 100;
      try {
        showFeedback(`Starting ${pagesVal}-page randomized galaxy sweep...`, "info");
        const res = await apiCommand2(
          { command: "galaxy_sweep", pages: pagesVal, dry_run: false },
          { timeoutMs: 3e5 }
        );
        showFeedback(res.message || "Galaxy sweep finished.", "ok");
        await loadDashboard({ refresh: false, force: true });
      } catch (err) {
        showFeedback(`Galaxy sweep failed: ${err.message}`, feedbackKindForError(err));
      }
    };
    const onRefreshUniverseResearch = async () => {
      try {
        showFeedback("Refreshing Universe Max Research levels and saving to SQLite...", "info");
        const res = await apiCommand2({ command: "universe_research_max", sync: true });
        if (Array.isArray(res.rows)) {
          setLocalResearchRows(res.rows);
        }
        showFeedback(
          res.message || "Universe Max Research table refreshed and saved to SQLite.",
          "ok"
        );
        await loadDashboard({ refresh: false, force: true });
      } catch (err) {
        showFeedback(
          `Universe Max Research refresh failed: ${err.message}`,
          feedbackKindForError(err)
        );
      }
    };
    const researchRows = Array.isArray(dashboard == null ? void 0 : dashboard.universe_research_max) ? dashboard.universe_research_max : localResearchRows || [];
    return /* @__PURE__ */ jsxRuntimeExports.jsx(
      "section",
      {
        id: "view-config",
        className: "page-view",
        "aria-label": "Strategy configuration",
        hidden: activePage !== "config",
        children: /* @__PURE__ */ jsxRuntimeExports.jsxs("article", { className: "hud-window page-fill", children: [
          /* @__PURE__ */ jsxRuntimeExports.jsxs("div", { className: "window-bar window-bar-wrap", children: [
            /* @__PURE__ */ jsxRuntimeExports.jsxs("div", { className: "window-title-group", children: [
              /* @__PURE__ */ jsxRuntimeExports.jsx("span", { className: "module-code tabular", children: "CFG" }),
              /* @__PURE__ */ jsxRuntimeExports.jsx("h2", { children: "Strategy Profile & Decision Buckets (SQLite)" })
            ] }),
            /* @__PURE__ */ jsxRuntimeExports.jsxs("div", { className: "inline-actions", children: [
              /* @__PURE__ */ jsxRuntimeExports.jsx(
                "button",
                {
                  type: "button",
                  className: "btn btn-secondary btn-sm",
                  id: "btn-refresh-universe-research",
                  onClick: onRefreshUniverseResearch,
                  children: "Refresh Max Research"
                }
              ),
              /* @__PURE__ */ jsxRuntimeExports.jsx(
                "button",
                {
                  type: "button",
                  className: "btn btn-secondary btn-sm",
                  id: "btn-claim-transmitter",
                  onClick: onClaimTransmitter,
                  children: "Claim Free IDT"
                }
              ),
              /* @__PURE__ */ jsxRuntimeExports.jsx(
                "button",
                {
                  type: "button",
                  className: "btn btn-secondary btn-sm",
                  id: "btn-galaxy-sweep",
                  onClick: onGalaxySweep,
                  children: "Sweep 100 Pages"
                }
              ),
              /* @__PURE__ */ jsxRuntimeExports.jsx(
                "button",
                {
                  type: "button",
                  className: "btn btn-primary btn-sm",
                  id: "btn-save-config",
                  onClick: onSaveConfig,
                  children: "Save & Apply"
                }
              )
            ] })
          ] }),
          /* @__PURE__ */ jsxRuntimeExports.jsxs("div", { className: "config-body", children: [
            CONFIG_GROUPS.map((group) => /* @__PURE__ */ jsxRuntimeExports.jsxs(
              "section",
              {
                className: "config-group",
                "aria-labelledby": group.headingId,
                children: [
                  /* @__PURE__ */ jsxRuntimeExports.jsxs("div", { className: "config-group-head", children: [
                    /* @__PURE__ */ jsxRuntimeExports.jsxs("div", { className: "config-group-title-row", children: [
                      /* @__PURE__ */ jsxRuntimeExports.jsx("h3", { id: group.headingId, children: group.title }),
                      group.id === "galaxy" && (dashboard == null ? void 0 : dashboard.galaxy_sweep) ? /* @__PURE__ */ jsxRuntimeExports.jsx(
                        "span",
                        {
                          className: "gal-cov-inline tabular",
                          id: "cfg-galaxy-coverage",
                          children: `Explored: ${Math.max(
                            0,
                            Math.min(100, safeFiniteNumber(dashboard.galaxy_sweep.coverage_pct, 0) ?? 0)
                          ).toFixed(1)}% (${Math.round(
                            safeFiniteNumber(dashboard.galaxy_sweep.explored_systems, 0) ?? 0
                          )}/${Math.round(
                            safeFiniteNumber(dashboard.galaxy_sweep.total_systems, 400) ?? 400
                          )} · ${String(dashboard.galaxy_sweep.scan_phase || "").toLowerCase() === "targeted_refresh" ? "Targeted Refresh" : "Exploratory"})`
                        }
                      ) : null
                    ] }),
                    /* @__PURE__ */ jsxRuntimeExports.jsx("p", { className: "config-group-sub", children: group.subtitle })
                  ] }),
                  /* @__PURE__ */ jsxRuntimeExports.jsx("div", { className: "config-list", children: group.items.map((item) => /* @__PURE__ */ jsxRuntimeExports.jsxs("div", { className: "config-item", children: [
                    /* @__PURE__ */ jsxRuntimeExports.jsxs("div", { className: "config-item-info", children: [
                      /* @__PURE__ */ jsxRuntimeExports.jsxs("div", { className: "config-item-head", children: [
                        /* @__PURE__ */ jsxRuntimeExports.jsx("label", { className: "config-item-label", htmlFor: item.id, children: item.title }),
                        /* @__PURE__ */ jsxRuntimeExports.jsx("code", { className: "config-item-key tabular", children: item.key }),
                        /* @__PURE__ */ jsxRuntimeExports.jsx("span", { className: "config-item-bounds tabular", children: item.boundsLabel })
                      ] }),
                      /* @__PURE__ */ jsxRuntimeExports.jsx("p", { className: "config-item-desc", children: item.description })
                    ] }),
                    /* @__PURE__ */ jsxRuntimeExports.jsx("div", { className: "config-item-control", children: item.control === "select" ? /* @__PURE__ */ jsxRuntimeExports.jsx(
                      "select",
                      {
                        id: item.id,
                        value: String(formValues[item.key] ?? item.defaultValue ?? ""),
                        onChange: (e) => markFieldChanged(item.key, e.target.value),
                        onInput: (e) => markFieldChanged(item.key, e.target.value),
                        children: (item.options || []).map((opt) => /* @__PURE__ */ jsxRuntimeExports.jsx("option", { value: opt.value, children: opt.label }, opt.value))
                      }
                    ) : item.control === "planet-select" ? /* @__PURE__ */ jsxRuntimeExports.jsxs(
                      "select",
                      {
                        id: item.id,
                        value: String(formValues[item.key] ?? "0"),
                        onChange: (e) => markFieldChanged(item.key, e.target.value),
                        onInput: (e) => markFieldChanged(item.key, e.target.value),
                        children: [
                          /* @__PURE__ */ jsxRuntimeExports.jsx("option", { value: "0", children: "0 — Auto (Active / Closest Colony with Probes)" }),
                          nonRaidPlanets.map((p) => {
                            var _a2;
                            return /* @__PURE__ */ jsxRuntimeExports.jsx("option", { value: String(p.planet_id), children: `${p.name || "Colony"} [${p.coords || "--"}] (#${p.planet_id} · s10: ${((_a2 = p.ships) == null ? void 0 : _a2.s10) ?? 0})` }, p.planet_id);
                          }),
                          !hasSavedPlanetOption ? /* @__PURE__ */ jsxRuntimeExports.jsx("option", { value: String(savedSpyPid), children: `Colony #${savedSpyPid} (Saved)` }) : null
                        ]
                      }
                    ) : item.control === "checkbox" ? /* @__PURE__ */ jsxRuntimeExports.jsx(
                      "input",
                      {
                        type: "checkbox",
                        className: "config-checkbox",
                        id: item.id,
                        checked: Boolean(formValues[item.key]),
                        onChange: (e) => markFieldChanged(item.key, e.target.checked)
                      }
                    ) : item.control === "text-seed" ? /* @__PURE__ */ jsxRuntimeExports.jsx(
                      "input",
                      {
                        type: "text",
                        inputMode: "numeric",
                        className: "config-seed-input",
                        id: item.id,
                        placeholder: item.placeholder || "Auto",
                        value: String(formValues[item.key] ?? ""),
                        onChange: (e) => markFieldChanged(item.key, e.target.value),
                        onInput: (e) => markFieldChanged(item.key, e.target.value)
                      }
                    ) : /* @__PURE__ */ jsxRuntimeExports.jsx(
                      "input",
                      {
                        type: "number",
                        id: item.id,
                        min: item.min,
                        max: item.max,
                        step: item.step,
                        placeholder: item.placeholder,
                        value: String(formValues[item.key] ?? item.defaultValue ?? ""),
                        onChange: (e) => markFieldChanged(item.key, e.target.value),
                        onInput: (e) => markFieldChanged(item.key, e.target.value)
                      }
                    ) })
                  ] }, item.id)) })
                ]
              },
              group.id
            )),
            /* @__PURE__ */ jsxRuntimeExports.jsxs(
              "section",
              {
                className: "config-research-section",
                id: "cfg-universe-research-section",
                "aria-labelledby": "cfg-grp-univ-research",
                children: [
                  /* @__PURE__ */ jsxRuntimeExports.jsxs("div", { className: "config-group-head", children: [
                    /* @__PURE__ */ jsxRuntimeExports.jsx("h3", { id: "cfg-grp-univ-research", children: "Universe Max Research Levels — Persisted in SQLite (universe_research_max)" }),
                    /* @__PURE__ */ jsxRuntimeExports.jsx("p", { className: "config-group-sub", children: "Highest observed research levels across server statistics, archived espionage reports, and your empire, stored monotonically in SQLite table universe_research_max. Click Refresh Max Research above to sync and persist the latest records." })
                  ] }),
                  /* @__PURE__ */ jsxRuntimeExports.jsx("div", { className: "table-wrap", children: /* @__PURE__ */ jsxRuntimeExports.jsxs("table", { id: "cfg-universe-research-max-table", children: [
                    /* @__PURE__ */ jsxRuntimeExports.jsx("thead", { children: /* @__PURE__ */ jsxRuntimeExports.jsxs("tr", { children: [
                      /* @__PURE__ */ jsxRuntimeExports.jsx("th", { children: "Research" }),
                      /* @__PURE__ */ jsxRuntimeExports.jsx("th", { className: "col-right", children: "Your Level" }),
                      /* @__PURE__ */ jsxRuntimeExports.jsx("th", { className: "col-right", children: "Universe Max" }),
                      /* @__PURE__ */ jsxRuntimeExports.jsx("th", { children: "Record Holder" }),
                      /* @__PURE__ */ jsxRuntimeExports.jsx("th", { className: "col-right", children: "Catalog Cap" }),
                      /* @__PURE__ */ jsxRuntimeExports.jsx("th", { className: "col-right", children: "Updated" })
                    ] }) }),
                    /* @__PURE__ */ jsxRuntimeExports.jsx("tbody", { id: "cfg-universe-research-max-tbody", children: researchRows.length === 0 ? /* @__PURE__ */ jsxRuntimeExports.jsx("tr", { children: /* @__PURE__ */ jsxRuntimeExports.jsx("td", { colSpan: 6, className: "empty-cell", children: "Click Refresh Max Research to populate and persist universe research records in SQLite." }) }) : researchRows.map((row) => {
                      const rKey = String(row.key || row.research_key || `r${row.research_id}`);
                      const ownLvl = Math.max(0, Math.round(safeFiniteNumber(row.own_level, 0) ?? 0));
                      const maxLvl = Math.max(
                        0,
                        Math.round(safeFiniteNumber(row.universe_max_level, 0) ?? 0)
                      );
                      const capLvl = safeFiniteNumber(row.catalog_max_level, null);
                      const holderTxt = [row.holder, row.holder_coords ? `[${row.holder_coords}]` : ""].filter(Boolean).join(" ") || "—";
                      return /* @__PURE__ */ jsxRuntimeExports.jsxs("tr", { children: [
                        /* @__PURE__ */ jsxRuntimeExports.jsxs("td", { children: [
                          /* @__PURE__ */ jsxRuntimeExports.jsx("span", { className: "action-title", children: row.name }),
                          " ",
                          /* @__PURE__ */ jsxRuntimeExports.jsxs("span", { className: "action-sub tabular", children: [
                            "(",
                            rKey,
                            ")"
                          ] })
                        ] }),
                        /* @__PURE__ */ jsxRuntimeExports.jsx("td", { className: "col-right tabular", children: `L${ownLvl}` }),
                        /* @__PURE__ */ jsxRuntimeExports.jsx("td", { className: "col-right tabular", children: `L${maxLvl}` }),
                        /* @__PURE__ */ jsxRuntimeExports.jsx("td", { children: holderTxt }),
                        /* @__PURE__ */ jsxRuntimeExports.jsx("td", { className: "col-right tabular", children: capLvl !== null && capLvl > 0 ? `L${Math.round(capLvl)}` : "Uncapped" }),
                        /* @__PURE__ */ jsxRuntimeExports.jsx("td", { className: "col-right tabular", children: fmtShortUpdated(row.updated_ts) })
                      ] }, rKey);
                    }) })
                  ] }) })
                ]
              }
            )
          ] })
        ] })
      }
    );
  };
  function formatDbSummary(db) {
    if (!db) return "SQLite database disabled (no persistent state directory configured).";
    const sch = db.schema || null;
    const verTag = (sch == null ? void 0 : sch.version_tag) || `V${String((sch == null ? void 0 : sch.version) || 0).padStart(3, "0")}`;
    const targetTag = (sch == null ? void 0 : sch.target_version_tag) || `V${String((sch == null ? void 0 : sch.target_version) || 0).padStart(3, "0")}`;
    const driftPart = Array.isArray(sch == null ? void 0 : sch.checksum_drift) && sch.checksum_drift.length > 0 ? ` · DRIFT(${sch.checksum_drift.length})` : "";
    const schemaLine = sch ? `Schema Version: ${verTag} / ${targetTag} (${sch.up_to_date ? "UP-TO-DATE" : `${sch.pending_count || 0} PENDING`}) · Checksum=${String(sch.schema_checksum || "").slice(0, 12) || "n/a"}${driftPart}` : "Schema Version: legacy";
    const lines = [
      `DB Path: ${db.db_path} (${fmtInt(db.size_bytes)} bytes)`,
      schemaLine,
      `Row Counts: ${Object.entries(db.counts || {}).map(([k, v]) => `${k}=${v}`).join(" · ")}`
    ];
    if (sch && Array.isArray(sch.applied_migrations) && sch.applied_migrations.length > 0) {
      lines.push("", "== Applied Schema Migrations ==");
      for (const m of sch.applied_migrations) {
        lines.push(`${m.version_tag} — ${m.name} (${fmtShortDateTime(m.applied_ts)})`);
      }
    }
    lines.push("", "== Tutorial Progress ==");
    const tutHistory = Array.isArray(db.tutorial_history) ? db.tutorial_history : [];
    for (const t of tutHistory) {
      const status = t.completed_ts ? "DONE" : t.can_complete ? "READY TO CLAIM" : "ACTIVE";
      const tStr = (Array.isArray(t.tasks) ? t.tasks : []).map((x) => x.description).join(" | ") || "(no tasks parsed)";
      lines.push(`Step ${t.step}/${t.total_steps} [${status}] ${tStr}`);
    }
    if (tutHistory.length === 0) {
      lines.push("(no tutorial rows recorded)");
    }
    lines.push("", "== Recent Actions ==");
    const recentActions = Array.isArray(db.recent_actions) ? db.recent_actions : [];
    for (const a of recentActions) {
      const mode = a.dry_run ? "DRY" : a.executed ? "EXEC" : "SKIP";
      const lvlPart = a.target_level != null ? ` -> ${a.target_level}` : "";
      lines.push(
        `[${mode}] ${a.kind} ${a.target_name || a.command}${lvlPart} @ ${a.planet_coords || "-"} — ${a.message || ""}`
      );
    }
    return lines.join("\n");
  }
  const LogsView = () => {
    const { dashboard, activePage, loadDashboard, uiPrefs, setUiPref } = useConsole();
    const activeLogSource = uiPrefs.activeLogSource || "commander";
    const logText = reactExports.useMemo(() => {
      var _a, _b;
      if (!dashboard) {
        return "Waiting for runtime log output...";
      }
      if (activeLogSource === "sqlite") {
        return formatDbSummary(dashboard.db_summary);
      }
      const src = activeLogSource === "game_api" ? (_a = dashboard.logs) == null ? void 0 : _a.game_api : (_b = dashboard.logs) == null ? void 0 : _b.commander;
      return src || "(Log file empty or not created yet.)";
    }, [activeLogSource, dashboard]);
    return /* @__PURE__ */ jsxRuntimeExports.jsx(
      "section",
      {
        id: "view-logs",
        className: "page-view",
        "aria-label": "Live runtime logs",
        hidden: activePage !== "logs",
        children: /* @__PURE__ */ jsxRuntimeExports.jsxs("article", { className: "hud-window page-fill", children: [
          /* @__PURE__ */ jsxRuntimeExports.jsxs("div", { className: "window-bar window-bar-wrap", children: [
            /* @__PURE__ */ jsxRuntimeExports.jsxs("div", { className: "window-title-group", children: [
              /* @__PURE__ */ jsxRuntimeExports.jsx("span", { className: "module-code tabular", children: "LOG" }),
              /* @__PURE__ */ jsxRuntimeExports.jsx("h2", { children: "Live Runtime Terminal Output" })
            ] }),
            /* @__PURE__ */ jsxRuntimeExports.jsxs("div", { className: "inline-actions", children: [
              /* @__PURE__ */ jsxRuntimeExports.jsxs("div", { className: "tab-group", role: "tablist", "aria-label": "Log source tabs", children: [
                /* @__PURE__ */ jsxRuntimeExports.jsx(
                  "button",
                  {
                    type: "button",
                    role: "tab",
                    "aria-selected": activeLogSource === "commander",
                    className: `tab-btn${activeLogSource === "commander" ? " active" : ""}`,
                    "data-log": "commander",
                    onClick: () => setUiPref("activeLogSource", "commander"),
                    children: "commander.log"
                  }
                ),
                /* @__PURE__ */ jsxRuntimeExports.jsx(
                  "button",
                  {
                    type: "button",
                    role: "tab",
                    "aria-selected": activeLogSource === "game_api",
                    className: `tab-btn${activeLogSource === "game_api" ? " active" : ""}`,
                    "data-log": "game_api",
                    onClick: () => setUiPref("activeLogSource", "game_api"),
                    children: "game_api.log"
                  }
                ),
                /* @__PURE__ */ jsxRuntimeExports.jsx(
                  "button",
                  {
                    type: "button",
                    role: "tab",
                    "aria-selected": activeLogSource === "sqlite",
                    className: `tab-btn${activeLogSource === "sqlite" ? " active" : ""}`,
                    "data-log": "sqlite",
                    onClick: () => setUiPref("activeLogSource", "sqlite"),
                    children: "SQLite DB"
                  }
                )
              ] }),
              /* @__PURE__ */ jsxRuntimeExports.jsx(
                "button",
                {
                  type: "button",
                  className: "btn btn-secondary btn-sm",
                  id: "btn-refresh-logs",
                  onClick: () => loadDashboard({ refresh: false, force: true }),
                  children: "Refresh"
                }
              )
            ] })
          ] }),
          /* @__PURE__ */ jsxRuntimeExports.jsx("div", { className: "logs-fill", children: /* @__PURE__ */ jsxRuntimeExports.jsx("pre", { id: "logs-pre", className: "console-box console-fill", children: logText }) })
        ] })
      }
    );
  };
  const App = ({ disableInitialFetch = false }) => {
    return /* @__PURE__ */ jsxRuntimeExports.jsx(ConsoleProvider, { disableInitialFetch, children: /* @__PURE__ */ jsxRuntimeExports.jsxs("div", { className: "hud-canvas", children: [
      /* @__PURE__ */ jsxRuntimeExports.jsxs("div", { className: "app-layout", children: [
        /* @__PURE__ */ jsxRuntimeExports.jsx(AppSidebar, {}),
        /* @__PURE__ */ jsxRuntimeExports.jsxs("div", { className: "main-column", children: [
          /* @__PURE__ */ jsxRuntimeExports.jsx(AppHeader, {}),
          /* @__PURE__ */ jsxRuntimeExports.jsx(FeedbackBanner, {}),
          /* @__PURE__ */ jsxRuntimeExports.jsxs("main", { className: "view-stage", id: "view-stage", children: [
            /* @__PURE__ */ jsxRuntimeExports.jsx(DashboardView, {}),
            /* @__PURE__ */ jsxRuntimeExports.jsx(OperationsView, {}),
            /* @__PURE__ */ jsxRuntimeExports.jsx(ActionGraphView, {}),
            /* @__PURE__ */ jsxRuntimeExports.jsx(BuildingsView, {}),
            /* @__PURE__ */ jsxRuntimeExports.jsx(CombatView, {}),
            /* @__PURE__ */ jsxRuntimeExports.jsx(EspionageView, {}),
            /* @__PURE__ */ jsxRuntimeExports.jsx(ColoniesView, {}),
            /* @__PURE__ */ jsxRuntimeExports.jsx(AttacksView, {}),
            /* @__PURE__ */ jsxRuntimeExports.jsx(RecyclersView, {}),
            /* @__PURE__ */ jsxRuntimeExports.jsx(RankingView, {}),
            /* @__PURE__ */ jsxRuntimeExports.jsx(ConfigView, {}),
            /* @__PURE__ */ jsxRuntimeExports.jsx(LogsView, {})
          ] })
        ] })
      ] }),
      /* @__PURE__ */ jsxRuntimeExports.jsx(HelpAlertModal, {}),
      /* @__PURE__ */ jsxRuntimeExports.jsx(NotificationCenterModal, {})
    ] }) });
  };
  function mountConsoleApp() {
    if (typeof document === "undefined") return;
    let rootEl = document.getElementById("root");
    if (!rootEl) {
      rootEl = document.createElement("div");
      rootEl.id = "root";
      document.body.replaceChildren(rootEl);
    } else {
      rootEl.replaceChildren();
    }
    const root = clientExports.createRoot(rootEl);
    reactDomExports.flushSync(() => {
      root.render(/* @__PURE__ */ jsxRuntimeExports.jsx(App, {}));
    });
  }
  if (typeof document !== "undefined") {
    if (document.readyState === "loading") {
      document.addEventListener("DOMContentLoaded", mountConsoleApp, { once: true });
    } else {
      mountConsoleApp();
    }
  }
})();
