"use strict";

/* ============================================================
   Turnero web multiusuario - cliente
   Cada usuario inicia sesión y ve SU turnero. El estado vive en el servidor
   (Neon); el cliente lo consulta cada 2 segundos.
   Todo el texto dinámico se inserta con textContent (sin innerHTML).
   ============================================================ */

const MODO_PANTALLA = new URLSearchParams(location.search).get("modo") === "pantalla";
if (MODO_PANTALLA) document.body.classList.add("pantalla");

const $ = (sel) => document.querySelector(sel);

let usuario = null;      // usuario con sesión iniciada
let estado = null;
let ultimoActualId;      // undefined = aún no se ha pintado nada
let enCurso = false;
let secuencia = 0;
let listaActiva = null;
let temporizador = null;
let cerrandoSesion = false;

function el(tag, clase, texto) {
  const n = document.createElement(tag);
  if (clase) n.className = clase;
  if (texto !== undefined && texto !== null) n.textContent = texto;
  return n;
}

function claveLS() { return `turnero_empleado_sugerido_${usuario ? usuario.id : 0}`; }
function leerLS(clave) {
  try { return localStorage.getItem(clave) || ""; } catch (_) { return ""; }
}
function guardarLS(clave, valor) {
  try { localStorage.setItem(clave, valor); } catch (_) { /* sin almacenamiento */ }
}

function fmtDur(s) {
  if (s === null || s === undefined) return "—";
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), x = Math.floor(s % 60);
  return [h, m, x].map((v) => String(v).padStart(2, "0")).join(":");
}

/* ---------------- API ---------------- */
async function api(url, { method = "GET", body, silencioso = false } = {}) {
  const headers = { "X-Requested-With": "turnero" };
  if (body) headers["Content-Type"] = "application/json";
  const resp = await fetch(url, {
    method,
    headers,
    body: body ? JSON.stringify(body) : undefined,
    credentials: "same-origin",
  });
  let data = {};
  try { data = await resp.json(); } catch (_) { /* respuesta sin JSON */ }
  if (!resp.ok) {
    const err = new Error(data.error || `Error ${resp.status}`);
    err.status = resp.status;
    if (resp.status === 401 && !silencioso) sesionTerminada();
    throw err;
  }
  return data;
}

function aplicar(nuevo) {
  secuencia++;            // invalida sondeos que estaban en vuelo
  estado = nuevo;
  render();
}

async function refrescar() {
  if (!usuario) return;
  const mi = ++secuencia;
  try {
    const e = await api("/api/estado");
    if (mi !== secuencia || !usuario) return;
    estado = e;
    render();
    $("#conexion").classList.remove("caida");
  } catch (_) {
    $("#conexion").classList.add("caida");
  }
}

function iniciarSondeo() {
  detenerSondeo();
  refrescar();
  temporizador = setInterval(refrescar, 2000);
}
function detenerSondeo() {
  if (temporizador) clearInterval(temporizador);
  temporizador = null;
}

/* ---------------- Sesión: login / salir ---------------- */
function mostrarLogin(mensaje) {
  detenerSondeo();
  usuario = null;
  estado = null;
  ultimoActualId = undefined;
  secuencia++;
  listaActiva = null;
  document.querySelectorAll("dialog[open]").forEach((d) => d.close());
  $("#app").hidden = true;
  $("#login").hidden = false;
  $("#login-error").textContent = mensaje || "";
  $("#login-password").value = "";
  $("#login-usuario").focus();
}

function sesionTerminada() {
  if (cerrandoSesion || !usuario) return;
  mostrarLogin("Tu sesión terminó. Inicia sesión de nuevo.");
}

async function salir() {
  cerrandoSesion = true;
  try { await api("/api/logout", { method: "POST", silencioso: true }); } catch (_) { /* da igual */ }
  cerrandoSesion = false;
  mostrarLogin("");
}

function pintarBarra() {
  const b = $("#barra-usuario");
  b.textContent = "";
  b.append(document.createTextNode(`👤 ${usuario.nombre} (${usuario.usuario}) `));
  const ins = el("span", "insignia", usuario.es_admin ? "Administrador" : "Usuario");
  ins.dataset.color = usuario.es_admin ? "morado" : "azulgris";
  b.append(ins);
  $("#btn-admin").hidden = !usuario.es_admin;
}

async function entrar(u) {
  usuario = u;
  estado = null;
  ultimoActualId = undefined;
  $("#login").hidden = true;
  $("#app").hidden = false;
  pintarBarra();
  $("#input-empleado").value = leerLS(claveLS());
  if (u.debe_cambiar) {
    const ok = await cambiarClave(true);
    if (!ok) return;
  }
  iniciarSondeo();
}

async function arrancar() {
  try {
    const r = await api("/api/me", { silencioso: true });
    await entrar(r.usuario);
  } catch (_) {
    mostrarLogin("");
  }
}

/* ---------------- Render ---------------- */
function destello() {
  const n = $("#turno-actual");
  n.classList.remove("flash");
  void n.offsetWidth;
  n.classList.add("flash");
}

function actualizarEtiquetaEmpleado() {
  const etiqueta = $("#etiqueta-empleado");
  if (estado && !estado.ajustes.empleado_obligatorio) {
    etiqueta.textContent = "Empleado: (modo opcional)";
    return;
  }
  const emp = $("#input-empleado").value.trim() || "(ninguno)";
  etiqueta.textContent = `Empleado: ${emp}`;
}

function render() {
  const e = estado;
  if (!e) return;

  $("#turno-espera").textContent = e.ultimo_espera || "—";
  $("#contador-espera").textContent = `Total en cola: ${e.espera.length} turnos`;

  const actual = e.actual;
  $("#turno-actual").textContent = actual ? actual.codigo : "—";
  $("#empleado-atencion").textContent =
    actual && actual.empleado ? `👤 Atiende: ${actual.empleado}` : "—";
  $("#contador-atencion").textContent = `Total en atención: ${e.atencion.length} turnos`;

  if (actual && ultimoActualId !== undefined && ultimoActualId !== actual.id) destello();
  ultimoActualId = actual ? actual.id : null;

  const bEmp = $("#btn-toggle-empleado");
  const obligatorio = e.ajustes.empleado_obligatorio;
  bEmp.textContent = obligatorio ? "🔒 Empleado: OBLIGATORIO" : "🔓 Empleado: OPCIONAL";
  bEmp.className = "btn " + (obligatorio ? "rojo" : "verde");

  const bCot = $("#btn-toggle-cotizacion");
  const cot = e.ajustes.cotizacion_activa;
  bCot.textContent = cot ? "📝 Cotización: ON" : "📝 Cotización: OFF";
  bCot.className = "btn " + (cot ? "cian" : "gris");

  actualizarEtiquetaEmpleado();

  const s = e.stats;
  $("#stats").textContent =
    `Sesión: ${e.sesion_inicio}  |  Generados: ${s.generados}  |  ` +
    `Atendidos: ${s.atendidos}  |  Limpiados: ${s.limpiados}  |  ` +
    `Espera: ${s.espera}  |  Atención: ${s.atencion}  |  ` +
    `Emp: ${obligatorio ? "OBLIGATORIO" : "OPCIONAL"}  |  Cot: ${cot ? "ON" : "OFF"}`;

  if (listaActiva) listaActiva.pintar();
}

/* ---------------- Diálogos ---------------- */
function dialogo({ titulo, color = "azul", clase = "", contenido = [], botones = [], onMontar, bloqueante = false }) {
  return new Promise((resolve) => {
    const dlg = el("dialog", `dlg ${clase}`.trim());
    dlg.dataset.color = color;
    let resultado = null;
    const ctl = {
      dlg,
      cerrar: (valor = null) => { resultado = valor; dlg.close(); },
    };
    if (bloqueante) dlg.addEventListener("cancel", (ev) => ev.preventDefault());

    const cuerpo = el("div", "dlg-cuerpo");
    contenido.forEach((n) => cuerpo.append(n));

    const pie = el("div", "dlg-pie");
    botones.forEach((b) => {
      const btn = el("button", `btn ${b.clase || "gris"}`, b.texto);
      btn.type = "button";
      btn.addEventListener("click", async () => {
        if (b.onClick) await b.onClick(ctl, btn);
        else ctl.cerrar(b.valor === undefined ? null : b.valor);
      });
      pie.append(btn);
    });

    dlg.append(el("h3", "dlg-titulo", titulo), cuerpo, pie);
    dlg.addEventListener("close", () => { dlg.remove(); resolve(resultado); });
    document.body.append(dlg);
    dlg.showModal();
    if (onMontar) onMontar(ctl);
  });
}

function aviso(titulo, mensaje, color = "azul") {
  return dialogo({
    titulo,
    color,
    contenido: [el("p", "dlg-msg", mensaje)],
    botones: [{ texto: "Aceptar", clase: color, valor: true }],
  });
}

function confirmar({ titulo, mensaje, color = "naranja", si = "Sí", no = "No" }) {
  return dialogo({
    titulo,
    color,
    contenido: [el("p", "dlg-msg", mensaje)],
    botones: [
      { texto: no, clase: "gris", valor: false },
      { texto: si, clase: color, valor: true },
    ],
    onMontar: (ctl) => {
      const btnNo = ctl.dlg.querySelector(".btn.gris");
      if (btnNo) btnNo.focus();     // por seguridad, el foco inicial es "No"
    },
  }).then((r) => r === true);
}

/* campos: [{name, label, tipo ('text'|'password'|'select'|'checkbox'), placeholder, maxlength,
             inputmode, valor, marcado, grande, autocomplete, limpiarEnError, opciones}]
   validar(valores) -> string con el error, o null si todo está bien (puede ser async) */
function formulario({ titulo, color = "morado", descripcion, info, campos, aceptar, cancelar = "Cancelar", validar, bloqueante = false }) {
  const err = el("div", "dlg-error");
  const inputs = {};
  const contenido = [];

  if (descripcion) contenido.push(el("p", "dlg-desc", descripcion));
  if (info) {
    const caja = el("div", "dlg-info");
    info.forEach(([k, v]) => {
      const linea = el("div");
      linea.append(el("b", null, k + ": "), document.createTextNode(v));
      caja.append(linea);
    });
    contenido.push(caja);
  }

  campos.forEach((c) => {
    const wrap = el("label", c.tipo === "checkbox" ? "campo check" : "campo");
    let inp;
    if (c.tipo === "select") {
      if (c.label) wrap.append(el("span", "campo-label", c.label));
      inp = el("select", "campo-input");
      c.opciones.forEach(([valor, texto]) => {
        const o = el("option", null, texto);
        o.value = valor;
        inp.append(o);
      });
    } else if (c.tipo === "checkbox") {
      inp = el("input");
      inp.type = "checkbox";
      inp.checked = !!c.marcado;
      wrap.append(inp);
      if (c.label) wrap.append(document.createTextNode(" " + c.label));
    } else {
      if (c.label) wrap.append(el("span", "campo-label", c.label));
      inp = el("input", "campo-input" + (c.grande ? " grande" : ""));
      inp.type = c.tipo || "text";
      if (c.placeholder) inp.placeholder = c.placeholder;
      if (c.maxlength) inp.maxLength = c.maxlength;
      if (c.inputmode) inp.inputMode = c.inputmode;
      inp.autocomplete = c.autocomplete || "off";
      if (c.valor) inp.value = c.valor;
    }
    inputs[c.name] = inp;
    if (c.tipo !== "checkbox") wrap.append(inp);
    contenido.push(wrap);
  });
  contenido.push(err);

  let validando = false;
  const enviar = async (ctl) => {
    if (validando) return;
    const valores = {};
    campos.forEach((c) => {
      valores[c.name] = c.tipo === "checkbox" ? inputs[c.name].checked : inputs[c.name].value;
    });

    validando = true;
    let msg = null;
    try {
      msg = validar ? await validar(valores) : null;
    } catch (e) {
      msg = "❌ " + e.message;
    } finally {
      validando = false;
    }

    if (msg) {
      err.textContent = msg;
      campos.forEach((c) => {
        if (c.limpiarEnError) inputs[c.name].value = "";
      });
      const foco = campos.find((c) => c.limpiarEnError);
      if (foco) inputs[foco.name].focus();
      Object.values(inputs).forEach((i) => {
        if (i.tagName === "INPUT" && i.type !== "checkbox") {
          i.classList.add("error");
          setTimeout(() => i.classList.remove("error"), 1500);
        }
      });
      return;
    }
    ctl.cerrar(valores);
  };

  return dialogo({
    titulo,
    color,
    contenido,
    bloqueante,
    botones: [
      { texto: cancelar, clase: "gris", valor: null },
      { texto: aceptar, clase: color, onClick: (ctl) => enviar(ctl) },
    ],
    onMontar: (ctl) => {
      const todos = campos.map((c) => inputs[c.name]);
      todos.forEach((inp, i) => {
        inp.addEventListener("keydown", (ev) => {
          if (ev.key !== "Enter") return;
          ev.preventDefault();
          if (i < todos.length - 1) todos[i + 1].focus();
          else enviar(ctl);
        });
        inp.addEventListener("input", () => { err.textContent = ""; });
      });
      if (todos[0]) {
        todos[0].focus();
        if (todos[0].select && todos[0].type !== "checkbox") todos[0].select();
      }
    },
  });
}

function envolver(tabla) {
  const w = el("div", "tabla-wrap");
  w.append(tabla);
  return w;
}

function tablaPares(pares) {
  const t = el("table", "tabla plana");
  const thead = el("thead");
  const trh = el("tr");
  ["Concepto", "Valor"].forEach((x) => trh.append(el("th", null, x)));
  thead.append(trh);
  const tb = el("tbody");
  pares.forEach(([k, v]) => {
    const tr = el("tr");
    tr.append(el("td", null, k), el("td", null, String(v)));
    tb.append(tr);
  });
  t.append(thead, tb);
  return envolver(t);
}

/* cols: [[titulo, clave | función(fila)]] */
function tablaDatos(cols, filas, vacio = "Sin datos todavía.") {
  if (!filas.length) return el("p", "vacio", vacio);
  const t = el("table", "tabla plana hist");
  const thead = el("thead");
  const trh = el("tr");
  cols.forEach(([titulo]) => trh.append(el("th", null, titulo)));
  thead.append(trh);
  const tb = el("tbody");
  filas.forEach((f) => {
    const tr = el("tr");
    cols.forEach(([, k]) => {
      const v = typeof k === "function" ? k(f) : f[k];
      tr.append(el("td", null, v === null || v === undefined || v === "" ? "—" : String(v)));
    });
    tb.append(tr);
  });
  t.append(thead, tb);
  return envolver(t);
}

/* ---------------- Ejecución protegida ---------------- */
async function ejecutar(fn) {
  if (enCurso) return;
  enCurso = true;
  try {
    await fn();
  } catch (e) {
    if (e.status !== 401) {          // 401: ya se mostró la pantalla de login
      await aviso("⚠️ Aviso", e.message, "naranja");
      refrescar();
    }
  } finally {
    enCurso = false;
  }
}

/* ---------------- Acciones del turnero ---------------- */
function nuevoTurno() {
  return ejecutar(async () => {
    const r = await api("/api/turnos", { method: "POST" });
    aplicar(r.estado);
  });
}

async function solicitarCotizacion(turno, manual) {
  const titulo = manual ? "📝 Registrar Cotización (Alt+C)" : "📝 Registro de Cotización";
  const v = await formulario({
    titulo,
    color: "cian",
    info: [["Turno", turno.codigo], ["Empleado", turno.empleado || "(sin empleado)"]],
    descripcion: "Ingresa los últimos 6 dígitos de la cotización generada:",
    campos: [
      { name: "cotizacion", placeholder: "Últimos 6 números (ej: 123456)", maxlength: 6, inputmode: "numeric", grande: true },
    ],
    aceptar: "✅ Registrar",
    cancelar: "Omitir",
    validar: async (val) => {
      const c = val.cotizacion.trim();
      if (!c) return "❌ Debes ingresar los últimos 6 dígitos";
      if (!/^[0-9]{6}$/.test(c)) return "❌ Deben ser exactamente 6 dígitos numéricos";
      try {
        const r = await api(`/api/turnos/${turno.id}/cotizacion`, { method: "POST", body: { cotizacion: c } });
        aplicar(r.estado);
        return null;
      } catch (e) {
        return "❌ " + e.message;
      }
    },
  });
  if (v && manual) {
    await aviso("✅ Cotización registrada", `Se registró la cotización ${v.cotizacion.trim()}\npara el turno ${turno.codigo}.`, "cian");
  }
}

function siguiente() {
  return ejecutar(async () => {
    if (!estado || !estado.espera.length) {
      await aviso("Sin turnos", "No hay turnos en espera para atender.");
      return;
    }

    let empleado = null;
    if (estado.ajustes.empleado_obligatorio && !estado.siguiente_empleado_pendiente) {
      const v = await formulario({
        titulo: "👤 Empleado que Atiende",
        color: "verde",
        descripcion: "Este empleado atenderá el siguiente turno:",
        campos: [
          { name: "empleado", placeholder: "Ej: VEN 02, T 001...", maxlength: 60, grande: true, valor: $("#input-empleado").value.trim() },
        ],
        aceptar: "✅ Llamar Turno",
        validar: (val) => (val.empleado.trim() ? null : "❌ Debes ingresar el número de empleado"),
      });
      if (!v) return;
      empleado = v.empleado.trim();
      $("#input-empleado").value = empleado;
      guardarLS(claveLS(), empleado);
    }

    const r = await api("/api/siguiente", { method: "POST", body: { empleado } });
    aplicar(r.estado);

    if (r.estado.ajustes.cotizacion_activa) {
      await solicitarCotizacion(r.turno, false);
    }
  });
}

function devolverTurno() {
  return ejecutar(async () => {
    if (!estado || !estado.atencion.length) {
      await aviso("Sin turnos", "No hay turnos en atención para devolver.");
      return;
    }
    const ok = await confirmar({
      titulo: "⚠️ Confirmar devolución",
      mensaje: `¿Estás seguro de devolver el turno ${estado.actual.codigo} a la cola de espera?\n\nEl turno volverá al inicio de la lista de espera.`,
    });
    if (!ok) return;
    const r = await api("/api/devolver", { method: "POST" });
    aplicar(r.estado);
  });
}

function cotizacionManual() {
  return ejecutar(async () => {
    if (!estado || !estado.actual) {
      await aviso("Sin turnos", "No hay turnos en atención para registrar cotización.");
      return;
    }
    const t = estado.actual;
    if (t.cotizacion) {
      await aviso(
        "✅ Cotización ya registrada",
        `El turno ${t.codigo} ya tiene una cotización registrada:\n\nCotización: ${t.cotizacion}\nEmpleado: ${t.empleado || "(sin empleado)"}\n\nNo se puede registrar una nueva cotización para este turno.`,
        "cian"
      );
      return;
    }
    await solicitarCotizacion(t, true);
  });
}

function limpiar(tipo) {
  const espera = tipo === "espera";
  return ejecutar(async () => {
    const base = `/api/limpiar/${tipo}`;
    const prev = await api(base, { method: "POST", body: { validar: true } });
    const ok = await confirmar({
      titulo: "⚠️ Confirmar limpieza",
      mensaje: espera
        ? `Se eliminarán los ${prev.total} turno(s) en espera.\n\n¿Deseas continuar?`
        : `Se eliminarán los ${prev.total} turno(s) en atención.\n\nLos tiempos de atención se registrarán hasta este momento.\n\n¿Deseas continuar?`,
    });
    if (!ok) return;
    const r = await api(base, { method: "POST", body: {} });
    aplicar(r.estado);
    await aviso(
      "Limpieza completada",
      `✅ Se han eliminado ${r.total} turno(s) ${espera ? "de la cola de espera" : "de atención"}.`,
      "verde"
    );
  });
}
const limpiarEspera = () => limpiar("espera");
const limpiarAtencion = () => limpiar("atencion");

function alternarEmpleado() {
  return ejecutar(async () => {
    const r = await api("/api/ajustes/empleado", { method: "POST" });
    aplicar(r.estado);

    let msg = "";
    if (r.pendientes_borrados > 0) {
      msg += `Se han eliminado ${r.pendientes_borrados} empleado(s) en memoria que estaban asociados a turnos devueltos.\n\nLos próximos turnos que se atiendan solicitarán un nuevo número de empleado (según el modo actual).\n\n`;
    }
    msg += r.estado.ajustes.empleado_obligatorio
      ? "🔒 Modo OBLIGATORIO activado.\nSe pedirá número de empleado cada vez que llames un turno."
      : "🔓 Modo OPCIONAL activado.\nNo se pedirá número de empleado al llamar turnos.";
    await aviso("Modo Empleado", msg, "morado");
  });
}

function alternarCotizacion() {
  return ejecutar(async () => {
    const r = await api("/api/ajustes/cotizacion", { method: "POST" });
    aplicar(r.estado);
    await aviso(
      "Modo Cotización",
      r.estado.ajustes.cotizacion_activa
        ? "📝 Cotización ON activado.\nAl atender cada turno se solicitará automáticamente los últimos 6 dígitos de la cotización generada."
        : "📝 Cotización OFF desactivado.\nNo se solicitará cotización al atender turnos.",
      "cian"
    );
  });
}

function reiniciar() {
  return ejecutar(async () => {
    await api("/api/reiniciar", { method: "POST", body: { validar: true } });
    const ok = await confirmar({
      titulo: "🔄 Confirmar Reinicio",
      mensaje:
        "¿Reiniciar el contador de turnos a 0?\n\nSe reiniciará:\n• Contador de turnos generados → 0\n\nNO se borrarán:\n• Total de turnos atendidos\n• Total de turnos limpiados\n• Historial de atenciones\n• Estadísticas de tiempo\n\nEsta acción no se puede deshacer.",
    });
    if (!ok) return;
    const r = await api("/api/reiniciar", { method: "POST", body: {} });
    aplicar(r.estado);
    await aviso("✅ Reinicio Completado", "El contador de turnos ha sido reiniciado a 0.\nLas estadísticas acumuladas permanecen intactas.", "verde");
  });
}

function cerrarJornada() {
  return ejecutar(async () => {
    const ok = await confirmar({
      titulo: "🏁 Cerrar jornada",
      color: "rojo",
      mensaje:
        "¿Cerrar la jornada actual?\n\nSe guardará el resumen en tu historial y el contador de turnos volverá a 0. Los turnos que sigan en espera o en atención se darán por cerrados.\n\nEsta acción no se puede deshacer.",
    });
    if (!ok) return;
    const r = await api("/api/sesion/cerrar", { method: "POST", body: {} });
    aplicar(r.estado);
    await dialogo({
      titulo: "📊 Resumen de la Jornada",
      color: "azul",
      clase: "ancho",
      contenido: [tablaPares(r.resumen)],
      botones: [{ texto: "Cerrar", clase: "azul", valor: true }],
    });
  });
}

async function mostrarHistorial() {
  return ejecutar(async () => {
    const { sesiones } = await api("/api/historial");
    const cols = [
      ["ID Sesión", "id_sesion"], ["Inicio", "fecha_inicio"], ["Cierre", "fecha_cierre"],
      ["Generados", "total_generados"], ["Atendidos", "total_atendidos"],
      ["En Espera", "en_espera_al_cerrar"], ["Limpiados", "total_limpiados"],
      ["T. Promedio Atención", "tiempo_promedio_atencion"], ["T. Máx. Atención", "tiempo_maximo_atencion"],
      ["Empleados Activos", (s) => Object.entries(s.turnos_por_empleado || {}).map(([k, v]) => `Emp ${k}: ${v}`).join(", ")],
    ];
    await dialogo({
      titulo: "📜 Historial de Jornadas",
      color: "naranja",
      clase: "ancho",
      contenido: [tablaDatos(cols, sesiones, "No hay jornadas registradas todavía.")],
      botones: [{ texto: "Cerrar", clase: "azul", valor: true }],
    });
  });
}

/* ---------------- Lista completa (espera / atención) ---------------- */
function abrirLista(tipo) {
  const espera = tipo === "espera";
  const color = espera ? "azul" : "verde";
  let seleccion = null;

  const instr = el("p", "dlg-desc", "💡 Selecciona un turno y presiona Retroceso (Backspace) para eliminarlo");
  const tabla = el("table", "tabla");
  const thead = el("thead");
  const trh = el("tr");
  ["#", "Turno", "Empleado", "Posición"].forEach((x) => trh.append(el("th", null, x)));
  thead.append(trh);
  const tbody = el("tbody");
  tabla.append(thead, tbody);
  const contador = el("div", "dlg-contador");

  function items() {
    return estado ? (espera ? estado.espera : estado.atencion) : [];
  }

  function pintar() {
    const lista = items();
    if (!lista.some((t) => t.id === seleccion)) seleccion = null;
    tbody.replaceChildren();
    lista.forEach((t, i) => {
      const tr = el("tr", t.id === seleccion ? "sel" : "");
      tr.append(
        el("td", null, String(i + 1)),
        el("td", "codigo", t.codigo),
        el("td", null, espera ? "—" : t.empleado || "—"),
        el("td", null, `${i + 1} de ${lista.length}`)
      );
      tr.addEventListener("click", () => { seleccion = t.id; pintar(); });
      tbody.append(tr);
    });
    contador.textContent = `Total: ${lista.length} turno(s)`;
  }

  function eliminar() {
    return ejecutar(async () => {
      if (seleccion === null) {
        await aviso("Sin selección", "Selecciona un turno de la lista para eliminarlo.", color);
        return;
      }
      const t = items().find((x) => x.id === seleccion);
      if (!t) return;
      const ok = await confirmar({ titulo: "Confirmar eliminación", mensaje: `¿Eliminar el turno ${t.codigo}?` });
      if (!ok) return;
      const r = await api(`/api/turnos/${t.id}`, { method: "DELETE" });
      const idx = items().findIndex((x) => x.id === t.id);
      aplicar(r.estado);
      const lista = items();
      if (lista.length) seleccion = lista[Math.min(idx, lista.length - 1)].id;
      pintar();
    });
  }

  listaActiva = { pintar };
  dialogo({
    titulo: espera ? "📋 Lista Completa de Turnos en Espera" : "📋 Lista Completa de Turnos en Atención",
    color,
    clase: "ancho",
    contenido: [instr, envolver(tabla), contador],
    botones: [
      { texto: "🗑️ Eliminar seleccionado", clase: "rosa", onClick: () => eliminar() },
      { texto: "Cerrar", clase: color, valor: true },
    ],
    onMontar: (ctl) => {
      ctl.dlg.addEventListener("keydown", (ev) => {
        if (ev.key === "Backspace") { ev.preventDefault(); eliminar(); }
      });
      pintar();
    },
  }).then(() => { listaActiva = null; });
}

/* ---------------- Mi contraseña ---------------- */
async function cambiarClave(obligatoria = false) {
  const v = await formulario({
    titulo: obligatoria ? "🔑 Elige una contraseña nueva" : "🔑 Cambiar mi contraseña",
    color: "naranja",
    bloqueante: obligatoria,
    descripcion: obligatoria ? "Por seguridad debes cambiar tu contraseña antes de continuar." : undefined,
    campos: [
      { name: "actual", label: "Contraseña actual:", tipo: "password", autocomplete: "current-password" },
      { name: "nueva", label: "Nueva contraseña (mín. 6 caracteres):", tipo: "password", autocomplete: "new-password" },
      { name: "confirmar", label: "Confirmar nueva contraseña:", tipo: "password", autocomplete: "new-password" },
    ],
    aceptar: "💾 Guardar",
    cancelar: obligatoria ? "Salir" : "Cancelar",
    validar: async (val) => {
      try {
        const r = await api("/api/password", { method: "POST", body: val });
        usuario = r.usuario;
        return null;
      } catch (e) {
        return "❌ " + e.message;
      }
    },
  });
  if (!v) {
    if (obligatoria) await salir();
    return false;
  }
  await aviso("Contraseña actualizada", "✅ Tu contraseña se cambió correctamente.\nLas demás sesiones abiertas con tu usuario se cerraron.", "verde");
  return true;
}

/* ---------------- Estadísticas detalladas ---------------- */
function tarjetas(lista) {
  const g = el("div", "kpis");
  lista.forEach(([valor, etiqueta]) => {
    const k = el("div", "kpi");
    k.append(el("b", null, String(valor)), el("span", null, etiqueta));
    g.append(k);
  });
  return g;
}

function titulo(texto, sub) {
  const n = [el("h4", "seccion", texto)];
  if (sub) n.push(el("p", "sub", sub));
  return n;
}

function colsEmpleado() {
  return [
    ["Empleado", "empleado"], ["Turnos", "turnos"], ["Cotizaciones", "cotizaciones"],
    ["T. promedio", "promedio_f"], ["T. mínimo", "minimo_f"], ["T. máximo", "maximo_f"], ["T. total", "total_f"],
  ];
}

function tarjetasBloque(b, extra = []) {
  const t = b.tiempos;
  return tarjetas([
    ...extra,
    [b.generados, "Turnos generados"],
    [b.atendidos, "Turnos atendidos"],
    [b.limpiados, "Turnos limpiados"],
    [b.cotizaciones, "Cotizaciones"],
    [b.empleados, "Empleados distintos"],
    [t.promedio_f, "Tiempo promedio"],
    [t.minimo_f, "Tiempo mínimo"],
    [t.maximo_f, "Tiempo máximo"],
    [t.total_f, "Tiempo total atendiendo"],
  ]);
}

function graficoHoras(filas) {
  if (!filas.length) return el("p", "vacio", "Sin datos todavía.");
  const max = Math.max(...filas.map((f) => f.turnos));
  const caja = el("div", "barras");
  filas.forEach((f) => {
    const fila = el("div", "barra-fila");
    const i = el("i");
    i.style.width = `${Math.max(3, Math.round((f.turnos / max) * 100))}%`;
    const pista = el("div");
    pista.append(i);
    fila.append(el("span", null, f.hora), pista, el("b", null, String(f.turnos)));
    caja.append(fila);
  });
  return caja;
}

function mostrarEstadisticas(usuarioId) {
  return ejecutar(async () => {
    const url = usuarioId && (!usuario || usuarioId !== usuario.id)
      ? `/api/estadisticas?usuario_id=${usuarioId}`
      : "/api/estadisticas";
    const d = await api(url);
    const a = d.actual;
    const h = d.historico;
    const propia = !usuarioId || usuarioId === usuario.id;

    const nodos = [];
    nodos.push(el("p", "sub", `Usuario: ${d.usuario.nombre} (${d.usuario.usuario}) · Generado: ${d.generado}`));

    nodos.push(...titulo("Jornada actual", `Inicio: ${a.inicio} · Empleado: ${a.modo_empleado} · Cotización: ${a.modo_cotizacion}`));
    nodos.push(tarjetasBloque(a, [[a.espera, "En espera ahora"], [a.atencion, "En atención ahora"]]));
    nodos.push(...titulo("Por empleado (jornada actual)"));
    nodos.push(tablaDatos(colsEmpleado(), a.por_empleado));
    nodos.push(...titulo("Detalle de turnos atendidos (jornada actual)"));
    nodos.push(tablaDatos(
      [
        ["Turno", "turno"], ["Empleado", "empleado"], ["Fecha", (r) => (r.fecha || "").split("-").reverse().join("/")],
        ["Llamado", "hora_llamado"], ["Fin", "hora_fin"], ["Duración", (r) => fmtDur(r.duracion_segundos)],
        ["Cotización", "cotizacion"], ["Limpiado", (r) => (r.limpiado ? "Sí" : "")],
      ],
      a.detalle,
      "Aún no se ha atendido ningún turno en esta jornada."
    ));

    nodos.push(...titulo("Acumulado de todas las jornadas", `Incluye la jornada actual y ${h.sesiones_cerradas} jornada(s) cerrada(s).`));
    nodos.push(tarjetasBloque(h, [[h.sesiones_cerradas, "Jornadas cerradas"]]));
    nodos.push(...titulo("Por empleado (acumulado)"));
    nodos.push(tablaDatos(colsEmpleado(), h.por_empleado));
    nodos.push(...titulo("Por día"));
    nodos.push(tablaDatos(
      [
        ["Fecha", "fecha"], ["Atendidos", "turnos"], ["Empleados", "empleados"], ["Cotizaciones", "cotizaciones"],
        ["T. promedio", "promedio_f"], ["T. mínimo", "minimo_f"], ["T. máximo", "maximo_f"], ["T. total", "total_f"],
      ],
      h.por_dia
    ));
    nodos.push(...titulo("Turnos atendidos por hora del día"));
    nodos.push(graficoHoras(h.por_hora));
    nodos.push(...titulo("Jornadas cerradas"));
    nodos.push(tablaDatos(
      [
        ["ID", "id_sesion"], ["Inicio", "fecha_inicio"], ["Cierre", "fecha_cierre"], ["Duración", "duracion"],
        ["Generados", "generados"], ["Atendidos", "atendidos"], ["Limpiados", "limpiados"],
        ["Cotizaciones", "cotizaciones"], ["T. promedio", "promedio"], ["T. máximo", "maximo"],
      ],
      d.sesiones,
      "Todavía no hay jornadas cerradas."
    ));

    dialogo({
      titulo: propia ? "📊 Mis estadísticas" : `📊 Estadísticas de ${d.usuario.nombre}`,
      color: "azulgris",
      clase: "ancho",
      contenido: nodos,
      botones: [
        { texto: "🔄 Actualizar", clase: "azul", onClick: async (ctl) => { ctl.cerrar(true); } },
        { texto: "Cerrar", clase: "gris", valor: false },
      ],
    }).then((recargar) => { if (recargar) mostrarEstadisticas(usuarioId); });
  });
}

/* ---------------- Administración de usuarios ---------------- */
function abrirAdmin() {
  if (!usuario || !usuario.es_admin) return;
  const tbody = el("tbody");
  const tabla = el("table", "tabla plana");
  const thead = el("thead");
  const trh = el("tr");
  ["Usuario", "Nombre", "Rol", "Acceso", "Último acceso", "Cola", "Acciones"].forEach((x) => trh.append(el("th", null, x)));
  thead.append(trh);
  tabla.append(thead, tbody);
  const ayuda = el("p", "dlg-desc",
    "Cada usuario trabaja con su propio turnero. Quitar el acceso cierra su sesión al instante; sus datos se conservan.");
  const msg = el("div", "dlg-error");
  let usuarios = [];

  function boton(texto, clase, fn, deshabilitado = false) {
    const b = el("button", `btn ${clase}`, texto);
    b.type = "button";
    b.disabled = deshabilitado;
    b.addEventListener("click", fn);
    return b;
  }

  async function cargar() {
    const r = await api("/api/admin/usuarios");
    usuarios = r.usuarios;
    pintar();
  }

  async function accion(fn) {
    msg.textContent = "";
    await ejecutar(async () => { await fn(); await cargar(); });
  }

  function pintar() {
    tbody.replaceChildren();
    usuarios.forEach((u) => {
      const yo = u.id === usuario.id;
      const tr = el("tr", u.activo ? "" : "inactivo");
      const celdaUsuario = el("td", "izq");
      celdaUsuario.append(document.createTextNode(u.usuario + (yo ? " (tú)" : "")));
      if (u.debe_cambiar) celdaUsuario.append(el("div", "sub", "debe cambiar su clave"));
      tr.append(
        celdaUsuario,
        el("td", "izq", u.nombre),
        el("td", null, u.es_admin ? "Administrador" : "Usuario"),
        el("td", null, u.activo ? "✅ Con acceso" : "⛔ Sin acceso"),
        el("td", null, u.ultimo_acceso || "Nunca"),
        el("td", null, `${u.en_espera} / ${u.en_atencion}`)
      );

      const acc = el("div", "acciones");
      acc.append(
        boton(u.activo ? "⛔ Quitar acceso" : "✅ Dar acceso", u.activo ? "rojo" : "verde", () => accion(async () => {
          if (u.activo) {
            const ok = await confirmar({ titulo: "Quitar acceso", mensaje: `¿Quitar el acceso a ${u.nombre} (${u.usuario})?\n\nSu sesión se cerrará de inmediato. Sus datos se conservan.` });
            if (!ok) return;
          }
          await api(`/api/admin/usuarios/${u.id}`, { method: "PATCH", body: { activo: !u.activo } });
        }), yo),
        boton(u.es_admin ? "Quitar admin" : "Hacer admin", "morado", () => accion(async () => {
          const ok = await confirmar({
            titulo: "Cambiar rol",
            mensaje: u.es_admin ? `${u.nombre} dejará de ser administrador.` : `${u.nombre} podrá crear usuarios y dar o quitar accesos.`,
          });
          if (!ok) return;
          await api(`/api/admin/usuarios/${u.id}`, { method: "PATCH", body: { es_admin: !u.es_admin } });
        }), yo),
        boton("🔑 Clave", "naranja", () => accion(async () => {
          const v = await formulario({
            titulo: `🔑 Nueva contraseña para ${u.usuario}`,
            color: "naranja",
            campos: [
              { name: "password", label: "Nueva contraseña (mín. 6 caracteres):", tipo: "password", autocomplete: "new-password" },
              { name: "debe_cambiar", label: "Pedirle que la cambie al entrar", tipo: "checkbox", marcado: true },
            ],
            aceptar: "💾 Guardar",
            validar: async (val) => {
              try {
                await api(`/api/admin/usuarios/${u.id}`, { method: "PATCH", body: val });
                return null;
              } catch (e) {
                return "❌ " + e.message;
              }
            },
          });
          if (v) await aviso("Contraseña actualizada", `La contraseña de ${u.usuario} se cambió y sus sesiones abiertas se cerraron.`, "verde");
        }), yo),
        boton("📊 Estadísticas", "azulgris", () => { mostrarEstadisticas(u.id); }),
        boton("♻️ Inicializar", "cafe", () => accion(async () => {
          const ok = await confirmar({
            titulo: "Inicializar turnero",
            mensaje: `Se cerrará la jornada de ${u.nombre} (queda en su historial) y su turnero volverá a empezar desde cero.\n\nLos turnos pendientes se darán por cerrados.\n\n¿Continuar?`,
          });
          if (!ok) return;
          const r = await api(`/api/admin/usuarios/${u.id}/inicializar`, { method: "POST", body: {} });
          await dialogo({
            titulo: `📊 Resumen de la jornada de ${u.usuario}`,
            color: "azul",
            clase: "ancho",
            contenido: [tablaPares(r.resumen)],
            botones: [{ texto: "Cerrar", clase: "azul", valor: true }],
          });
        }))
      );
      const td = el("td");
      td.append(acc);
      tr.append(td);
      tbody.append(tr);
    });
  }

  function nuevoUsuario() {
    return accion(async () => {
      const v = await formulario({
        titulo: "➕ Nuevo usuario",
        color: "verde",
        descripcion: "Su turnero se crea vacío y listo para usar.",
        campos: [
          { name: "usuario", label: "Usuario (letras, números . _ -):", maxlength: 40, autocomplete: "off" },
          { name: "nombre", label: "Nombre completo:", maxlength: 80 },
          { name: "password", label: "Contraseña inicial (mín. 6 caracteres):", tipo: "password", autocomplete: "new-password" },
          { name: "es_admin", label: "Es administrador", tipo: "checkbox" },
          { name: "debe_cambiar", label: "Pedirle que cambie la clave al entrar", tipo: "checkbox", marcado: true },
        ],
        aceptar: "✅ Crear",
        validar: async (val) => {
          try {
            await api("/api/admin/usuarios", { method: "POST", body: val });
            return null;
          } catch (e) {
            return "❌ " + e.message;
          }
        },
      });
      if (v) await aviso("Usuario creado", `✅ Se creó el usuario ${v.usuario.trim().toLowerCase()}.\nYa puede iniciar sesión con la contraseña que le asignaste.`, "verde");
    });
  }

  dialogo({
    titulo: "👥 Administración de usuarios",
    color: "morado",
    clase: "ancho",
    contenido: [ayuda, envolver(tabla), msg],
    botones: [
      { texto: "➕ Nuevo usuario", clase: "verde", onClick: () => nuevoUsuario() },
      { texto: "Cerrar", clase: "gris", valor: true },
    ],
    onMontar: () => { cargar().catch((e) => { msg.textContent = "❌ " + e.message; }); },
  });
}

/* ---------------- Eventos ---------------- */
function init() {
  $("#form-login").addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const err = $("#login-error");
    const btn = $("#login-enviar");
    err.textContent = "";
    btn.disabled = true;
    try {
      const r = await api("/api/login", {
        method: "POST",
        silencioso: true,
        body: { usuario: $("#login-usuario").value, password: $("#login-password").value },
      });
      $("#login-password").value = "";
      await entrar(r.usuario);
    } catch (e) {
      err.textContent = "❌ " + e.message;
      $("#login-password").value = "";
      $("#login-password").focus();
    } finally {
      btn.disabled = false;
    }
  });

  $("#input-empleado").addEventListener("input", (ev) => {
    guardarLS(claveLS(), ev.target.value.trim());
    actualizarEtiquetaEmpleado();
  });

  $("#btn-stats").addEventListener("click", () => mostrarEstadisticas());
  $("#btn-admin").addEventListener("click", abrirAdmin);
  $("#btn-clave").addEventListener("click", () => ejecutar(() => cambiarClave(false)));
  $("#btn-salir").addEventListener("click", salir);

  $("#btn-nuevo").addEventListener("click", nuevoTurno);
  $("#btn-lista-espera").addEventListener("click", () => abrirLista("espera"));
  $("#btn-limpiar-espera").addEventListener("click", limpiarEspera);

  $("#btn-siguiente").addEventListener("click", siguiente);
  $("#btn-anterior").addEventListener("click", devolverTurno);
  $("#btn-lista-atencion").addEventListener("click", () => abrirLista("atencion"));
  $("#btn-limpiar-atencion").addEventListener("click", limpiarAtencion);

  $("#btn-toggle-empleado").addEventListener("click", alternarEmpleado);
  $("#btn-toggle-cotizacion").addEventListener("click", alternarCotizacion);
  $("#btn-cotizar").addEventListener("click", cotizacionManual);
  $("#btn-reiniciar").addEventListener("click", reiniciar);
  $("#btn-historial").addEventListener("click", mostrarHistorial);
  $("#btn-cerrar-sesion").addEventListener("click", cerrarJornada);
  $("#btn-pantalla").addEventListener("click", () => window.open("/?modo=pantalla", "_blank"));

  // Atajos: Alt+letra (Ctrl+N/L/K... los reservan los navegadores) y Retroceso.
  document.addEventListener("keydown", (ev) => {
    if (MODO_PANTALLA || !usuario || $("#app").hidden) return;
    if (document.querySelector("dialog[open]")) return;

    const tag = (ev.target.tagName || "").toLowerCase();
    const escribiendo = tag === "input" || tag === "textarea" || tag === "select" || ev.target.isContentEditable;

    if (ev.key === "Backspace" && !escribiendo && !ev.altKey && !ev.ctrlKey && !ev.metaKey) {
      ev.preventDefault();
      devolverTurno();
      return;
    }
    if (!ev.altKey || ev.ctrlKey || ev.metaKey) return;

    const mapa = {
      KeyN: nuevoTurno,
      KeyS: siguiente,
      KeyA: devolverTurno,
      KeyL: limpiarEspera,
      KeyK: limpiarAtencion,
      KeyC: cotizacionManual,
    };
    const fn = mapa[ev.code];
    if (fn) { ev.preventDefault(); fn(); }
  });

  // Doble clic en el modo pantalla: pantalla completa.
  document.addEventListener("dblclick", () => {
    if (!MODO_PANTALLA) return;
    if (document.fullscreenElement) document.exitFullscreen();
    else document.documentElement.requestFullscreen().catch(() => {});
  });

  arrancar();
}

init();
