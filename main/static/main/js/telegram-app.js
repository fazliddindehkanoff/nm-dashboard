(() => {
  'use strict';
  const tg = window.Telegram && window.Telegram.WebApp;
  if (tg) {
    tg.ready();
    tg.expand();
    if (!tg.isVersionAtLeast || tg.isVersionAtLeast('6.1')) {
      tg.setHeaderColor('#00213D');
      tg.setBackgroundColor('#F7FAFC');
    }
  }

  const state = {
    profile: null,
    courses: [],
    myCourses: [],
    purchases: [],
    course: null,
    type: 'self',
    purchase: null,
    legal: null,
    legalReadOnly: false,
    legalReturnView: 'homeView',
    selectedCourse: null,
  };
  const $ = (selector, root = document) => root.querySelector(selector);
  const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
  const money = value => `${new Intl.NumberFormat('uz-UZ', { maximumFractionDigits: 2 }).format(Number(value || 0))} UZS`;
  const monthNames = ['yanvar', 'fevral', 'mart', 'aprel', 'may', 'iyun', 'iyul', 'avgust', 'sentabr', 'oktabr', 'noyabr', 'dekabr'];
  const formatDate = value => {
    if (!value) return 'Sana biriktirilmagan';
    const [year, month, day] = value.slice(0, 10).split('-').map(Number);
    return `${day} ${monthNames[month - 1]} ${year}`;
  };
  const formatDateTime = value => {
    if (!value) return '';
    const parsed = new Date(value);
    const date = `${parsed.getDate()} ${monthNames[parsed.getMonth()]}`;
    const time = `${String(parsed.getHours()).padStart(2, '0')}:${String(parsed.getMinutes()).padStart(2, '0')}`;
    return `${date}, ${time}`;
  };
  const initials = name => (name || 'N').split(/\s+/).slice(0, 2).map(v => v[0]).join('').toUpperCase();
  const escapeHtml = value => String(value ?? '').replace(/[&<>'"]/g, char => ({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[char]));
  const csrf = $('meta[name="csrf-token"]').content;

  function headers(json = false) {
    const result = { 'X-Telegram-Init-Data': tg?.initData || '' };
    if (document.body.dataset.demo === '1') result['X-Telegram-Demo'] = '1';
    if (json) Object.assign(result, { 'Content-Type': 'application/json', 'X-CSRFToken': csrf });
    return result;
  }

  async function api(url, options = {}) {
    const response = await fetch(url, { ...options, headers: { ...headers(Boolean(options.body)), ...(options.headers || {}) } });
    const data = await response.json().catch(() => ({ ok: false, error: 'Server javobi noto‘g‘ri.' }));
    if (!response.ok || !data.ok) throw new Error(data.error || 'Xatolik yuz berdi.');
    return data;
  }

  let toastTimer;
  function toast(message, error = false) {
    const node = $('#toast'); node.textContent = message; node.classList.toggle('is-error', error); node.classList.add('is-visible');
    clearTimeout(toastTimer); toastTimer = setTimeout(() => node.classList.remove('is-visible'), 3200);
  }

  function setJourney(step) {
    $$('.journey__step').forEach((node, index) => node.classList.toggle('is-active', index < step));
    $$('.journey__line').forEach((node, index) => { node.style.background = index < step - 1 ? '#3BC9D4' : ''; });
  }

  function showView(id, step = 1) {
    $$('.view').forEach(node => node.classList.toggle('is-active', node.id === id));
    $('.hero').style.display = id === 'homeView' ? '' : 'none';
    $('.bottom-nav').style.display = ['homeView', 'coursesView', 'profileView'].includes(id) ? '' : 'none';
    setJourney(step); window.scrollTo({ top: 0, behavior: 'smooth' });
  }

  function setMainNav(name) {
    $$('.bottom-nav button').forEach(node => node.classList.toggle('is-active', node.dataset.nav === name));
  }

  function configureLegalGate(kind, documentData, readOnly = false) {
    const prefix = kind === 'terms' ? 'terms' : 'contract';
    const scroll = $(`#${prefix}Scroll`);
    const progress = $(`#${prefix}Progress`);
    const consent = $(`#${prefix}Consent`);
    const button = $(`#accept${kind === 'terms' ? 'Terms' : 'Contract'}`);
    const intro = $(`#${prefix}Intro`);
    const label = consent.closest('.legal-consent');
    scroll.innerHTML = documentData.html;
    scroll.scrollTop = 0;
    consent.checked = false;
    state.legal = { kind, version: documentData.version };
    state.legalReadOnly = readOnly;

    if (readOnly) {
      label.hidden = true;
      button.disabled = false;
      button.textContent = 'Yopish';
      intro.textContent = kind === 'terms'
        ? 'Siz ushbu versiyani avval qabul qilgansiz. To‘liq matnni istalgan vaqtda qayta ko‘rishingiz mumkin.'
        : 'Ushbu shartnoma elektron ravishda qabul qilingan. Xaridga biriktirilgan to‘liq matn quyida saqlanadi.';
    } else {
      label.hidden = false;
      consent.disabled = true;
      button.disabled = true;
      button.textContent = 'Oxirigacha o‘qing';
      intro.textContent = kind === 'terms'
        ? 'Davom etishdan oldin hujjatni oxirigacha o‘qing. Roziligingiz sana, qurilma va hujjat versiyasi bilan saqlanadi.'
        : 'Shartnoma aynan shu xarid ma’lumotlari asosida tayyorlandi. Oxirigacha o‘qigach tasdiqlash faollashadi.';
    }

    const updateGate = () => {
      const maximum = Math.max(scroll.scrollHeight - scroll.clientHeight, 0);
      const ratio = maximum ? Math.min(scroll.scrollTop / maximum, 1) : 1;
      progress.style.width = `${Math.round(ratio * 100)}%`;
      if (!readOnly && ratio >= .995) {
        consent.disabled = false;
        button.textContent = kind === 'terms' ? 'Qabul qilaman' : 'Shartnomani qabul qilaman';
        button.disabled = !consent.checked;
      }
    };
    scroll.onscroll = updateGate;
    consent.onchange = updateGate;
    requestAnimationFrame(() => {
      scroll.scrollTop = 0;
      updateGate();
    });
  }

  async function openTerms(required = false) {
    try {
      const data = await api('/telegram-app/api/legal/terms/');
      state.legalReturnView = required ? 'homeView' : 'profileView';
      configureLegalGate('terms', data.document, !required && data.document.accepted);
      showView('termsView', 1);
    } catch (error) {
      toast(error.message, true);
    }
  }

  async function openContract(purchase, readOnly = false) {
    try {
      state.purchase = purchase;
      const data = await api(`/telegram-app/api/purchases/${purchase.id}/contract/`);
      const current = { ...purchase, contract_accepted: data.document.accepted };
      state.purchase = current;
      replacePurchase(current);
      state.legalReturnView = readOnly ? 'homeView' : 'homeView';
      configureLegalGate('contract', data.document, readOnly && data.document.accepted);
      showView('contractView', 2);
    } catch (error) {
      toast(error.message, true);
    }
  }

  function renderProfile() {
    const name = state.profile.full_name || 'Foydalanuvchi';
    $('#welcomeTitle').textContent = `Salom, ${name.split(' ')[0]}`;
    $('#profileName').textContent = name; $('#profilePhone').textContent = state.profile.phone_number || '—';
    $('#profileAvatar').textContent = initials(name); $('#profileButton').textContent = initials(name);
    $('#selfParticipant').innerHTML = `<span class="person-dot">${escapeHtml(initials(name))}</span><div><strong>${escapeHtml(name)}</strong><small>${escapeHtml(state.profile.phone_number)}</small></div>`;
  }

  function courseStats(course) {
    const lessons = course.participants.flatMap(participant => participant.lessons || []);
    const attended = lessons.filter(lesson => ['attended', 'late'].includes(lesson.status)).length;
    const marked = lessons.filter(lesson => lesson.status !== 'unmarked').length;
    return { attended, marked, total: lessons.length };
  }

  function renderLearningOverview() {
    const activeCourses = state.myCourses.filter(course => course.is_active && course.assignment_status === 'assigned');
    const totals = state.myCourses.reduce((sum, course) => {
      const stats = courseStats(course);
      sum.attended += stats.attended;
      sum.marked += stats.marked;
      return sum;
    }, { attended: 0, marked: 0 });
    $('#heroCourseSummary').textContent = activeCourses.length
      ? `${activeCourses.length} ta faol kurs · ${totals.attended} ta qatnashuv`
      : 'Faol kurs hali biriktirilmagan';

    const host = $('#learningOverview');
    if (!state.myCourses.length) {
      host.innerHTML = `<article class="overview-empty"><span class="overview-empty__mark">N</span><div><strong>Kurslaringiz shu yerda ko‘rinadi</strong><p>Faol guruhdan kurs tanlang yoki markaz biriktirgan guruhni kuzating.</p></div></article>`;
      return;
    }

    const featured = activeCourses[0] || state.myCourses[0];
    const stats = courseStats(featured);
    const progress = stats.total ? Math.round(stats.attended * 100 / stats.total) : 0;
    host.innerHTML = `
      <div class="overview-metrics">
        <article><small>Faol kurs</small><strong>${activeCourses.length}</strong></article>
        <article><small>Qatnashilgan</small><strong>${totals.attended}</strong></article>
        <article><small>Qayd etilgan</small><strong>${totals.marked}</strong></article>
      </div>
      <button class="overview-featured" type="button" data-overview-course="${escapeHtml(featured.id)}">
        <span class="overview-featured__top"><span>${featured.assignment_status === 'assigned' ? 'Davomat yo‘li' : 'Guruh kutilmoqda'}</span><strong>${stats.attended}/${stats.total || featured.number_of_days}</strong></span>
        <span class="overview-featured__title">${escapeHtml(featured.course)}</span>
        <span class="overview-featured__track"><i style="width:${progress}%"></i></span>
        <span class="overview-featured__foot">${featured.assignment_status === 'assigned' ? `${featured.participants.length} ishtirokchi · ${formatDate(featured.start_date)}` : 'To‘lov qabul qilingan · guruh hali biriktirilmagan'}<b>Ochish →</b></span>
      </button>`;
    $('[data-overview-course]', host).addEventListener('click', () => openCourseDetail(featured.id));
  }

  function renderCourses() {
    $('#courseCount').textContent = `${state.courses.length} ta`;
    $('#courseList').innerHTML = state.courses.length ? state.courses.map(course => `
      <article class="course-card">
        <div class="course-card__top"><span class="course-mark"><img src="/static/main/brand/norbekov-mark.svg" alt=""></span>
          <div><span class="availability"><i></i> Faol guruh bor</span><h3>${escapeHtml(course.name)}</h3><p>${course.number_of_days || 0} kunlik rivojlanish dasturi</p></div>
        </div>
        <div class="course-card__schedule"><span><small>Eng yaqin guruh</small><strong>${formatDate(course.active_groups[0]?.start_date)}</strong></span><span><small>Ustoz</small><strong>${escapeHtml(course.active_groups[0]?.teachers?.join(', ') || 'Tez orada')}</strong></span></div>
        <div class="course-card__bottom"><div class="price"><small>Bir kishi uchun</small><strong>${money(course.price)}</strong></div>
          <button class="select-button" type="button" data-course-id="${course.id}">Tanlash</button></div>
      </article>`).join('') : '<article class="course-card"><h3>Hozircha faol guruh yo‘q</h3><p>Yangi guruh ochilganda kurs shu yerda paydo bo‘ladi.</p></article>';
    $$('[data-course-id]').forEach(button => button.addEventListener('click', () => startCheckout(Number(button.dataset.courseId))));
  }

  function renderMyCourses() {
    $('#myCourseCount').textContent = `${state.myCourses.length} ta`;
    const host = $('#myCourseList');
    if (!state.myCourses.length) {
      host.innerHTML = `<article class="my-course-empty"><span>+</span><div><strong>Hali xarid qilingan kurs yo‘q</strong><p>Quyidagi faol guruhlardan birini tanlang.</p></div></article>`;
      return;
    }
    host.innerHTML = state.myCourses.map(course => {
      const stats = courseStats(course);
      const awaiting = course.assignment_status === 'awaiting_group';
      const progress = stats.total ? Math.round(stats.attended * 100 / stats.total) : 0;
      const stateLabel = awaiting ? 'Guruh kutilmoqda' : course.is_active ? 'Faol guruh' : 'Yakunlangan';
      const tone = awaiting ? 'waiting' : course.is_active ? 'active' : 'complete';
      return `
        <button class="my-course-card" type="button" data-my-course="${escapeHtml(course.id)}">
          <span class="my-course-card__head"><span class="course-state course-state--${tone}">${stateLabel}</span><span>${course.participants.length} ishtirokchi</span></span>
          <strong class="my-course-card__title">${escapeHtml(course.course)}</strong>
          <span class="my-course-card__meta">${awaiting ? 'Markaz guruhni biriktirgach darslar ko‘rinadi' : `${formatDate(course.start_date)} · ${course.number_of_days} kun`}</span>
          <span class="my-course-card__progress"><i style="width:${progress}%"></i></span>
          <span class="my-course-card__foot"><span>${awaiting ? 'Davomat hali boshlanmagan' : `${stats.attended} ta qatnashuv · ${stats.marked} ta qayd`}</span><b>Batafsil →</b></span>
        </button>`;
    }).join('');
    $$('[data-my-course]', host).forEach(button => button.addEventListener('click', () => openCourseDetail(button.dataset.myCourse)));
  }

  function renderHistory() {
    const host = $('#purchaseHistory');
    const pendingPurchases = state.purchases.filter(item => item.payment_status !== 'success' || !item.questionnaire_completed);
    if (!pendingPurchases.length) { host.innerHTML = ''; return; }
    host.innerHTML = `<h2 class="history-heading">Yakunlanmagan xaridlar</h2>${pendingPurchases.map(item => {
      const done = item.questionnaire_completed && item.payment_status === 'success';
      const paid = item.payment_status === 'success';
      const label = done ? 'Tayyor' : paid ? 'Anketa kutilmoqda' : item.payment_status_label;
      const contractLink = item.contract_accepted ? `<button type="button" class="continue-button" data-view-contract="${item.id}">Shartnomani ko‘rish</button>` : '';
      return `<article class="purchase-card"><div class="purchase-card__head"><div><h3>${escapeHtml(item.course)}</h3><p>${item.participant_count} ishtirokchi · ${money(item.total_amount)}</p></div><span class="status status--${done ? 'success' : 'pending'}">${label}</span></div>${done ? contractLink : `<button type="button" class="continue-button" data-resume="${item.id}">Davom ettirish →</button>${contractLink}`}</article>`;
    }).join('')}`;
    $$('[data-resume]').forEach(button => button.addEventListener('click', () => resumePurchase(Number(button.dataset.resume))));
    $$('[data-view-contract]').forEach(button => button.addEventListener('click', () => {
      const purchase = state.purchases.find(item => item.id === Number(button.dataset.viewContract));
      if (purchase) openContract(purchase, true);
    }));
  }

  function lessonTone(status) {
    return ['attended', 'late', 'absent', 'excused'].includes(status) ? status : 'unmarked';
  }

  function renderCourseDetail(course) {
    state.selectedCourse = course;
    const awaiting = course.assignment_status === 'awaiting_group';
    $('#courseDetailTitle').textContent = course.course;
    const stateNode = $('#courseDetailState');
    stateNode.textContent = awaiting ? 'Guruh kutilmoqda' : course.is_active ? 'Faol guruh' : 'Yakunlangan';
    stateNode.className = `course-state course-state--${awaiting ? 'waiting' : course.is_active ? 'active' : 'complete'}`;
    $('#courseDetailMeta').innerHTML = awaiting ? `
      <div><small>Holat</small><strong>Guruh biriktirilmoqda</strong></div>
      <div><small>Davomiyligi</small><strong>${course.number_of_days} kun</strong></div>` : `
      <div><small>Boshlanish</small><strong>${formatDate(course.start_date)}</strong></div>
      <div><small>Davomiyligi</small><strong>${course.number_of_days} kun</strong></div>
      <div class="course-detail-meta__wide"><small>Ustoz</small><strong>${escapeHtml(course.teachers.join(', ') || 'Biriktirilmoqda')}</strong></div>`;

    const participantsHost = $('#courseDetailParticipants');
    if (awaiting) {
      participantsHost.innerHTML = `<article class="attendance-awaiting"><span class="attendance-awaiting__icon">⌁</span><h3>Davomat guruh bilan ochiladi</h3><p>Markaz sizni guruhga biriktirgach, har bir dars sanasi va davomat qaydi shu sahifada ko‘rinadi.</p><div>${course.participants.map(item => `<span>${escapeHtml(item.full_name)}</span>`).join('')}</div></article>`;
      return;
    }

    participantsHost.innerHTML = course.participants.map(participant => {
      const stats = courseStats({ participants: [participant] });
      return `<section class="participant-attendance">
        <div class="participant-attendance__head"><span class="person-dot">${escapeHtml(initials(participant.full_name))}</span><div><small>Ishtirokchi</small><h3>${escapeHtml(participant.full_name)}</h3><p>${escapeHtml(participant.status_label)}</p></div><strong>${stats.attended}/${participant.lessons.length}</strong></div>
        <div class="attendance-summary"><span><small>Qatnashdi</small><strong>${stats.attended}</strong></span><span><small>Qayd qilindi</small><strong>${stats.marked}</strong></span><span><small>Oxirgi kelgan</small><strong>${participant.last_attended_at ? formatDate(participant.last_attended_at) : '—'}</strong></span></div>
        <div class="attendance-timeline">${participant.lessons.map(lesson => `
          <article class="lesson-row lesson-row--${lessonTone(lesson.status)}">
            <span class="lesson-row__rail"><i></i></span>
            <div class="lesson-row__body">
              <div class="lesson-row__top"><span><small>${lesson.day_number}-dars</small><strong>${formatDate(lesson.date)}</strong></span><b>${escapeHtml(lesson.status_label)}</b></div>
              <p>${lesson.marked_at ? `${formatDateTime(lesson.marked_at)} · ${escapeHtml(lesson.marked_by || 'Markaz xodimi')} tomonidan belgilandi` : 'Hali davomat qaydi kiritilmagan'}</p>
              ${lesson.reason ? `<em>Sabab: ${escapeHtml(lesson.reason)}</em>` : ''}
              ${lesson.note ? `<em>Izoh: ${escapeHtml(lesson.note)}</em>` : ''}
            </div>
          </article>`).join('')}</div>
      </section>`;
    }).join('');
  }

  function openCourseDetail(courseId) {
    const course = state.myCourses.find(item => String(item.id) === String(courseId));
    if (!course) return;
    renderCourseDetail(course);
    showView('courseDetailView', 1);
  }

  function startCheckout(courseId) {
    state.course = state.courses.find(course => course.id === courseId); state.purchase = null; state.type = 'self';
    $('#checkoutCourse').textContent = state.course.name; $$('.segment__item').forEach((node, i) => node.classList.toggle('is-active', i === 0));
    $('#familyArea').hidden = true; $('#familyMembers').innerHTML = ''; updateTotal(); showView('checkoutView', 1);
  }

  function updateTotal() {
    const participants = 1 + (state.type === 'family' ? $$('.family-member').length : 0);
    $('#liveTotal').textContent = money(Number(state.course?.price || 0) * participants);
    $('#participantLabel').textContent = `${participants} ishtirokchi`;
  }

  function addFamilyMember() {
    if ($$('.family-member').length >= 7) return toast('Ko‘pi bilan 7 ta oila a’zosi qo‘shiladi.', true);
    const number = $$('.family-member').length + 1;
    const node = document.createElement('div'); node.className = 'family-member';
    node.innerHTML = `<div class="family-member__number">${number}-oila a’zosi</div><button class="remove-member" type="button" aria-label="O‘chirish">×</button><div class="field"><label>To‘liq ism</label><input name="full_name" autocomplete="name" placeholder="Ism Familiya" required></div><div class="field"><label>Telefon raqami</label><input name="phone_number" type="tel" autocomplete="tel" placeholder="+998 90 123 45 67" required></div>`;
    $('.remove-member', node).addEventListener('click', () => { node.remove(); renumberMembers(); updateTotal(); });
    $('#familyMembers').append(node); updateTotal();
  }

  function renumberMembers() { $$('.family-member__number').forEach((node, index) => { node.textContent = `${index + 1}-oila a’zosi`; }); }

  async function createPurchase() {
    const button = $('#continueToPayment');
    const members = $$('.family-member').map(node => ({ full_name: $('[name="full_name"]', node).value.trim(), phone_number: $('[name="phone_number"]', node).value.trim() }));
    if (state.type === 'family' && !members.length) return toast('Kamida bitta oila a’zosini qo‘shing.', true);
    if (members.some(member => !member.full_name || !member.phone_number)) return toast('Oila a’zolari ma’lumotlarini to‘liq kiriting.', true);
    button.disabled = true;
    try {
      const data = await api('/telegram-app/api/purchases/', { method: 'POST', body: JSON.stringify({ course_id: state.course.id, purchase_type: state.type, members }) });
      state.purchase = data.purchase; state.purchases.unshift(data.purchase); renderHistory(); openContract(data.purchase);
    } catch (error) { toast(error.message, true); } finally { button.disabled = false; }
  }

  function renderPayment() {
    const p = state.purchase;
    if (!p.contract_accepted) {
      openContract(p);
      return;
    }
    $('#paymentSummary').innerHTML = `<div class="summary-row"><span>Kurs</span><strong>${escapeHtml(p.course)}</strong></div><div class="summary-row"><span>Xarid turi</span><strong>${escapeHtml(p.purchase_type_label)}</strong></div><div class="summary-row"><span>Ishtirokchilar</span><strong>${p.participant_count} kishi</strong></div><div class="summary-row summary-total"><span>Jami</span><strong>${money(p.total_amount)}</strong></div>`;
    $('#contractVersionLabel').textContent = `Versiya ${p.contract_version}`;
    const demo = document.body.dataset.demo === '1';
    $('#paymentNote').textContent = demo ? 'Demo to‘lov: mablag‘ yechilmaydi.' : 'To‘lov Multicard sahifasida amalga oshiriladi. To‘lovdan keyin Telegram ilovasiga qayting.';
    $('#paymentStatus').textContent = p.invoice_state === 'uncertain' || p.invoice_state === 'creating'
      ? 'To‘lov holati aniqlanmoqda. Qayta to‘lamang; administrator bilan bog‘laning.'
      : p.payment_status_label;
    $('#payButton').disabled = ['creating', 'uncertain', 'revert'].includes(p.invoice_state) || p.payment_status === 'refunded';
    $('#checkPayment').hidden = demo || !p.invoice_state;
    $('#payButton').textContent = p.checkout_url ? 'To‘lov sahifasini ochish' : 'To‘lovni amalga oshirish';
  }

  async function acceptTerms() {
    if (state.legalReadOnly) return showView(state.legalReturnView, 1);
    const button = $('#acceptTerms'); button.disabled = true; button.textContent = 'Saqlanmoqda…';
    try {
      await api('/telegram-app/api/legal/terms/accept/', {
        method: 'POST',
        body: JSON.stringify({ accepted: true, version: state.legal.version }),
      });
      toast('Foydalanish shartlari qabul qilindi.');
      showView('homeView', 1);
    } catch (error) {
      toast(error.message, true);
      button.disabled = false;
      button.textContent = 'Qabul qilaman';
    }
  }

  async function acceptContract() {
    if (state.legalReadOnly) return showView(state.legalReturnView, 1);
    const button = $('#acceptContract'); button.disabled = true; button.textContent = 'Saqlanmoqda…';
    try {
      const data = await api(`/telegram-app/api/purchases/${state.purchase.id}/contract/accept/`, {
        method: 'POST',
        body: JSON.stringify({ accepted: true, version: state.legal.version }),
      });
      state.purchase = data.purchase;
      replacePurchase(data.purchase);
      renderPayment();
      showView('paymentView', 2);
      toast('Shartnoma qabul qilindi. Endi to‘lovni amalga oshirishingiz mumkin.');
    } catch (error) {
      toast(error.message, true);
      button.disabled = false;
      button.textContent = 'Shartnomani qabul qilaman';
    }
  }

  function applyPayment(purchase) {
    replacePurchase(purchase);
    if (state.purchase?.id !== purchase.id) return;
    state.purchase = purchase;
    if (purchase.payment_status === 'success') {
      if (!$('#paymentView').classList.contains('is-active')) return;
      if (purchase.questionnaire_completed) showView('successView', 3);
      else { renderQuestionnaires(); showView('questionnaireView', 3); }
      toast('To‘lov muvaffaqiyatli qabul qilindi.');
    } else { renderPayment(); }
  }

  async function startPayment() {
    const purchaseId = state.purchase.id;
    const button = $('#payButton'); button.disabled = true; button.textContent = 'To‘lov tayyorlanmoqda…';
    try {
      const endpoint = document.body.dataset.demo === '1' ? 'demo-payment' : 'payment';
      const data = await api(`/telegram-app/api/purchases/${purchaseId}/${endpoint}/`, { method: 'POST', body: '{}' });
      applyPayment(data.purchase);
      if (data.purchase.payment_status !== 'success' && data.checkout_url) {
        const url = new URL(data.checkout_url);
        if (url.protocol !== 'https:') throw new Error('To‘lov havolasi noto‘g‘ri.');
        if (tg?.initData && tg.openLink) tg.openLink(url.href);
        else window.location.assign(url.href);
        $('#paymentStatus').textContent = 'To‘lov kutilmoqda. Yakunlagach, holatni tekshiring.';
      }
    } catch (error) { toast(error.message, true); }
    finally { if (state.purchase?.id === purchaseId) renderPayment(); }
  }

  let checkingPayment = false;
  async function checkPayment(remote = false) {
    if (checkingPayment || !state.purchase || !$('#paymentView').classList.contains('is-active')) return;
    checkingPayment = true;
    const purchaseId = state.purchase.id;
    $('#checkPayment').disabled = true;
    try {
      const path = remote ? 'check' : 'status';
      const data = await api(`/telegram-app/api/purchases/${purchaseId}/payment/${path}/`, remote ? { method: 'POST', body: '{}' } : {});
      applyPayment(data.purchase);
      if (remote && data.purchase.payment_status !== 'success') toast(data.purchase.payment_status_label);
    } catch (error) { if (remote) toast(error.message, true); }
    finally { checkingPayment = false; $('#checkPayment').disabled = false; }
  }

  function replacePurchase(purchase) { const index = state.purchases.findIndex(item => item.id === purchase.id); if (index >= 0) state.purchases[index] = purchase; else state.purchases.unshift(purchase); renderHistory(); }

  function renderQuestionnaires() {
    $('#questionnaireMembers').innerHTML = state.purchase.members.map((member, index) => `
      <article class="questionnaire-card" data-member-id="${member.id}"><h3>${escapeHtml(member.full_name)}</h3><p>${index === 0 ? 'Xaridor' : 'Oila a’zosi'} · ${escapeHtml(member.phone_number)}</p>
        <div class="field"><label>Tug‘ilgan sana *</label><input type="date" name="birth_date" required></div>
        <div class="field"><label>Shahar / tuman *</label><input name="city" placeholder="Masalan: Toshkent, Chilonzor" required></div>
        <div class="field"><label>Kasb / faoliyat</label><input name="occupation" placeholder="Faoliyatingiz"></div>
        <div class="field"><label>Kursdan maqsadingiz *</label><textarea name="learning_goal" placeholder="Nimaga erishmoqchisiz?" required></textarea></div>
        <div class="field"><label>Oldingi tajriba</label><textarea name="prior_experience" placeholder="Shunga o‘xshash kurslarda qatnashganmisiz?"></textarea></div>
        <div class="field"><label>Muhim sog‘liq izohlari</label><textarea name="health_notes" placeholder="Bilishimiz kerak bo‘lgan ma’lumot (ixtiyoriy)"></textarea></div>
        <label class="consent"><input type="checkbox" name="consent" required><span>Ma’lumotlar to‘g‘ri ekanini tasdiqlayman va ulardan kursni tashkil etish uchun foydalanishga roziman.</span></label>
      </article>`).join('');
  }

  async function submitQuestionnaire(event) {
    event.preventDefault();
    if (!event.currentTarget.reportValidity()) return;
    const button = $('button[type="submit"]', event.currentTarget); button.disabled = true;
    const responses = $$('.questionnaire-card').map(card => ({ member_id: Number(card.dataset.memberId), birth_date: $('[name="birth_date"]', card).value, city: $('[name="city"]', card).value.trim(), occupation: $('[name="occupation"]', card).value.trim(), learning_goal: $('[name="learning_goal"]', card).value.trim(), prior_experience: $('[name="prior_experience"]', card).value.trim(), health_notes: $('[name="health_notes"]', card).value.trim(), consent: $('[name="consent"]', card).checked }));
    try {
      const data = await api(`/telegram-app/api/purchases/${state.purchase.id}/questionnaire/`, { method: 'POST', body: JSON.stringify({ responses }) });
      state.purchase = data.purchase; replacePurchase(data.purchase); showView('successView', 3);
    } catch (error) { toast(error.message, true); } finally { button.disabled = false; }
  }

  function resumePurchase(id) {
    state.purchase = state.purchases.find(item => item.id === id);
    if (state.purchase.payment_status === 'success') { renderQuestionnaires(); showView('questionnaireView', 3); }
    else if (!state.purchase.contract_accepted) { openContract(state.purchase); }
    else { renderPayment(); showView('paymentView', 2); }
  }

  function goHome() { showView('homeView', 1); renderHistory(); setMainNav('home'); }

  function showCourses() {
    renderMyCourses();
    renderCourses();
    showView('coursesView', 1);
    setMainNav('courses');
  }

  async function bootstrap() {
    try {
      const data = await api('/telegram-app/api/bootstrap/'); Object.assign(state, { profile: data.profile, courses: data.courses, myCourses: data.my_courses || [], purchases: data.purchases });
      renderProfile(); renderLearningOverview(); renderMyCourses(); renderCourses(); renderHistory();
      if (data.legal.terms_required) openTerms(true);
    } catch (error) {
      $('#courseList').innerHTML = `<article class="course-card"><h3>Ilovani ochib bo‘lmadi</h3><p>${escapeHtml(error.message)} Telegram bot ichidagi tugma orqali qayta urinib ko‘ring.</p></article>`;
      toast(error.message, true);
    }
  }

  $$('.segment__item').forEach(button => button.addEventListener('click', () => { state.type = button.dataset.purchaseType; $$('.segment__item').forEach(node => node.classList.toggle('is-active', node === button)); $('#familyArea').hidden = state.type !== 'family'; if (state.type === 'family' && !$$('.family-member').length) addFamilyMember(); updateTotal(); }));
  $('#addFamilyMember').addEventListener('click', addFamilyMember); $('#continueToPayment').addEventListener('click', createPurchase); $('#payButton').addEventListener('click', startPayment); $('#questionnaireForm').addEventListener('submit', submitQuestionnaire);
  $('#acceptTerms').addEventListener('click', acceptTerms); $('#acceptContract').addEventListener('click', acceptContract);
  $('#viewTerms').addEventListener('click', () => openTerms(false)); $('#viewContract').addEventListener('click', () => openContract(state.purchase, true));
  $('#heroCoursesButton').addEventListener('click', showCourses); $('#openCoursesFromHome').addEventListener('click', showCourses); $('#courseDetailBack').addEventListener('click', showCourses);
  $('#contractBack').addEventListener('click', goHome); $('#checkoutBack').addEventListener('click', goHome); $('#paymentBack').addEventListener('click', () => state.purchase ? goHome() : showView('checkoutView', 1));
  $('#profileButton').addEventListener('click', () => { showView('profileView', 1); setMainNav('profile'); }); $$('[data-go-home]').forEach(node => node.addEventListener('click', goHome));
  $$('[data-nav]').forEach(button => button.addEventListener('click', () => {
    if (button.dataset.nav === 'profile') { showView('profileView', 1); setMainNav('profile'); }
    else if (button.dataset.nav === 'courses') showCourses();
    else goHome();
  }));
  $('#checkPayment').addEventListener('click', () => checkPayment(true));
  window.addEventListener('focus', () => checkPayment());
  document.addEventListener('visibilitychange', () => { if (!document.hidden) checkPayment(); });
  setInterval(() => { if (!document.hidden) checkPayment(); }, 5000);
  bootstrap();
})();
