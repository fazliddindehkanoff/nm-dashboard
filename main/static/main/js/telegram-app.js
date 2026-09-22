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
    const result = { 'X-Telegram-Init-Data': tg?.initData || '', 'X-CSRFToken': csrf };
    if (document.body.dataset.demo === '1') result['X-Telegram-Demo'] = '1';
    if (json) Object.assign(result, { 'Content-Type': 'application/json', 'X-CSRFToken': csrf });
    return result;
  }

  async function api(url, options = {}) {
    const response = await fetch(url, { ...options, headers: { ...headers(typeof options.body === 'string'), ...(options.headers || {}) } });
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

  function renderCourseGroups(course) {
    return `<ul class="course-groups" aria-label="${escapeHtml(course.name)} — faol guruhlar">${course.active_groups.map(group => `
      <li class="course-group" data-group-id="${group.id}">
        <p class="course-group__status">${group.can_purchase ? 'Qabul ochiq' : 'Boshlangan guruh'}</p>
        ${group.banner_url ? `<img class="course-banner" src="${escapeHtml(group.banner_url)}" alt="${escapeHtml(course.name)} — ${formatDate(group.start_date)}" loading="lazy">` : ''}
        <div class="course-card__schedule">
          <span><small>Boshlanish sanasi</small><strong>${formatDate(group.start_date)}</strong></span>
          <span><small>Davomiyligi</small><strong>${group.number_of_days} kun</strong></span>
          <span class="course-group__teachers"><small>Ustoz</small><strong>${escapeHtml(group.teachers?.join(', ') || 'Tez orada')}</strong></span>
        </div>
      </li>`).join('')}</ul>`;
  }

  function renderCourses() {
    $('#courseCount').textContent = `${state.courses.length} ta`;
    $('#courseList').innerHTML = state.courses.length ? state.courses.map(course => `
      <article class="course-card">
        <div class="course-card__top"><span class="course-mark"><img src="/static/main/brand/norbekov-mark.svg" alt=""></span>
          <div><span class="availability"><i></i> ${course.active_groups.length} ta faol guruh</span><h3>${escapeHtml(course.name)}</h3><p>${course.number_of_days || 0} kunlik rivojlanish dasturi</p></div>
        </div>
        ${renderCourseGroups(course)}
        <div class="course-card__bottom"><div class="price"><small>Bir kishi uchun</small><strong>${money(course.price)}</strong></div>
          <button class="select-button" type="button" data-course-id="${course.id}" ${course.can_purchase ? '' : 'disabled'}>${course.can_purchase ? 'Tanlash' : 'Qabul yopilgan'}</button></div>
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
      return `<article class="purchase-card"><div class="purchase-card__head"><div><h3>${escapeHtml(item.course)}</h3><p>${item.participant_count} ishtirokchi · ${money(item.total_amount)}</p>${item.is_booking ? `<p>To‘langan: ${money(item.paid_amount)} · Qolgan: ${money(item.payable_amount)}</p>` : ''}</div><span class="status status--${done ? 'success' : 'pending'}">${label}</span></div>${done ? contractLink : `<button type="button" class="continue-button" data-resume="${item.id}">Davom ettirish →</button>${contractLink}`}</article>`;
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
    $('#selfEligibility').innerHTML = eligibilityFields(); bindEligibility($('#selfEligibility'));
    $('#paymentMode').value = 'booking'; $('#familyArea').hidden = true; $('#familyMembers').innerHTML = ''; updateTotal(); showView('checkoutView', 1);
  }

  function updateTotal() {
    const participants = 1 + (state.type === 'family' ? $$('.family-member').length : 0);
    const price = Number(state.course?.price || 0);
    const discount = Math.min(price, Math.max(0, ...(state.course?.participant_discounts || [])
      .filter(rule => participants >= rule.min_participants).map(rule => Number(rule.amount))));
    const eligibilityNodes = [$('#selfEligibility'), ...(state.type === 'family' ? $$('.family-member') : [])];
    const eligible = eligibilityNodes.filter(node => $('[name="eligibility_category"]', node)?.value).length;
    const socialDiscount = Math.min(100000, price - discount) * eligible;
    const total = (price - discount) * participants - socialDiscount;
    $('#socialDiscountLabel').textContent = socialDiscount ? `Hujjat asosida qo‘shimcha chegirma: −${money(socialDiscount)}` : '';
    $('#liveTotal').textContent = money(total);
    $('#participantLabel').textContent = `${participants} ishtirokchi`;
    $('#discountLabel').hidden = !discount;
    $('#discountLabel').textContent = discount ? `Chegirma: har bir kishiga −${money(discount)}` : '';
    const bookingDiscount = Math.min(total, Math.min(price - discount, Number(state.course?.booking_discount || 0)) * participants);
    const minimum = Number(state.course?.minimum_booking || 100000) * participants;
    const net = total - bookingDiscount;
    const bookingOption = $('#paymentMode option[value="booking"]');
    bookingOption.disabled = net < minimum;
    if (bookingOption.disabled) $('#paymentMode').value = 'full';
    $('#bookingQuote').textContent = $('#paymentMode').value === 'booking'
      ? `Har bir kishi uchun kamida ${money(state.course.minimum_booking)}. ${participants} kishiga eng kam bron: ${money(minimum)}. To‘lov tasdiqlangach jami ${money(bookingDiscount)} bron chegirmasi qo‘llanadi.`
      : 'Kursning to‘liq summasi bir martada to‘lanadi.';
  }

  function eligibilityFields() {
    return `<div class="eligibility-fields"><label class="field">Qo‘shimcha chegirma<select name="eligibility_category"><option value="">Toifaga kirmayman</option><option value="pensioner">Pensioner</option><option value="disability">Nogironligi bor</option><option value="student">Talaba</option></select></label><label class="field eligibility-file" hidden>Tasdiqlovchi hujjat<input type="file" name="eligibility_file" accept=".pdf,.jpg,.jpeg,.png"><small>PDF, JPG yoki PNG, 5 MB gacha. Hujjat asosida 100 000 so‘mgacha chegirma.</small></label></div>`;
  }

  function bindEligibility(node) {
    $('[name="eligibility_category"]', node).addEventListener('change', event => {
      $('.eligibility-file', node).hidden = !event.target.value;
      delete node.dataset.proofKey; delete node.dataset.proofId; updateTotal();
    });
    $('[name="eligibility_file"]', node).addEventListener('change', () => { delete node.dataset.proofKey; });
  }

  async function uploadProof(node, phone) {
    const category = $('[name="eligibility_category"]', node).value;
    if (!category) return null;
    const file = $('[name="eligibility_file"]', node).files[0];
    if (!file || file.size > 5 * 1024 * 1024) throw new Error('Chegirma uchun 5 MB gacha tasdiqlovchi hujjat yuboring.');
    const key = `${category}:${phone}:${file.name}:${file.size}:${file.lastModified}`;
    if (node.dataset.proofKey === key) return Number(node.dataset.proofId);
    const body = new FormData(); body.append('category', category); body.append('phone_number', phone); body.append('document', file);
    const result = await api('/telegram-app/api/eligibility-documents/', { method: 'POST', body });
    node.dataset.proofKey = key; node.dataset.proofId = result.id;
    return result.id;
  }

  function addFamilyMember() {
    if ($$('.family-member').length >= 7) return toast('Ko‘pi bilan 7 ta oila a’zosi qo‘shiladi.', true);
    const number = $$('.family-member').length + 1;
    const node = document.createElement('div'); node.className = 'family-member';
    node.innerHTML = `<div class="family-member__number">${number}-oila a’zosi</div><button class="remove-member" type="button" aria-label="O‘chirish">×</button><div class="field"><label>To‘liq ism</label><input name="full_name" autocomplete="name" placeholder="Ism Familiya" required></div><div class="field"><label>Telefon raqami</label><input name="phone_number" type="tel" autocomplete="tel" placeholder="+998 90 123 45 67" required></div>`;
    node.insertAdjacentHTML('beforeend', eligibilityFields()); bindEligibility(node);
    $('.remove-member', node).addEventListener('click', () => { node.remove(); renumberMembers(); updateTotal(); });
    $('#familyMembers').append(node); updateTotal();
  }

  function renumberMembers() { $$('.family-member__number').forEach((node, index) => { node.textContent = `${index + 1}-oila a’zosi`; }); }

  async function createPurchase() {
    const button = $('#continueToPayment');
    const memberNodes = state.type === 'family' ? $$('.family-member') : [];
    const members = memberNodes.map(node => ({ full_name: $('[name="full_name"]', node).value.trim(), phone_number: $('[name="phone_number"]', node).value.trim() }));
    if (state.type === 'family' && !members.length) return toast('Kamida bitta oila a’zosini qo‘shing.', true);
    if (members.some(member => !member.full_name || !member.phone_number)) return toast('Oila a’zolari ma’lumotlarini to‘liq kiriting.', true);
    button.disabled = true;
    try {
      const eligibility_document_id = await uploadProof($('#selfEligibility'), state.profile.phone_number);
      for (let index = 0; index < members.length; index++) members[index].eligibility_document_id = await uploadProof(memberNodes[index], members[index].phone_number);
      const data = await api('/telegram-app/api/purchases/',  { method: 'POST', body: JSON.stringify({ course_id: state.course.id, purchase_type: state.type, members, eligibility_document_id, payment_mode: $('#paymentMode').value }) });
      state.purchase = data.purchase; state.purchases.unshift(data.purchase); renderHistory(); openContract(data.purchase);
    } catch (error) { toast(error.message, true); } finally { button.disabled = false; }
  }

  function renderPayment() {
    const p = state.purchase;
    if (!p.contract_accepted) {
      openContract(p);
      return;
    }
    const openInvoice = ['creating', 'uncertain', 'ready', 'error'].includes(p.invoice_state);
    const row = (label, value) => `<div class="summary-row"><span>${label}</span><strong>${value}</strong></div>`;
    $('#paymentSummary').innerHTML = row('Kurs', escapeHtml(p.course))
      + row('Ishtirokchilar', `${p.participant_count} kishi`)
      + row('Kurs narxi — jami', money(Number(p.total_amount) + Number(p.discount_total || 0) + Number(p.social_discount_amount || 0)))
      + (Number(p.discount_total) > 0 ? row(escapeHtml(p.discount_name), `−${money(p.discount_total)}`) : '')
      + (Number(p.social_discount_amount) > 0 ? row('Hujjat asosidagi chegirma', `−${money(p.social_discount_amount)}`) : '')
      + (p.is_booking ? row(Number(p.paid_amount) > 0 ? 'Bron chegirmasi' : 'To‘lovdan keyingi bron chegirmasi', `−${money(p.booking_discount)}`) : '')
      + row('To‘langan', money(p.paid_amount))
      + row('Qolgan to‘lov', money(p.payable_amount));
    $('#contractVersionLabel').textContent = `Versiya ${p.contract_version}`;
    const demo = document.body.dataset.demo === '1';
    $('#paymentNote').textContent = demo ? 'Demo to‘lov: mablag‘ yechilmaydi.' : 'To‘lov Multicard sahifasida amalga oshiriladi. To‘lovdan keyin Telegram ilovasiga qayting.';
    $('#paymentStatus').textContent = p.invoice_state === 'uncertain' || p.invoice_state === 'creating'
      ? 'To‘lov holati aniqlanmoqda. Qayta to‘lamang; administrator bilan bog‘laning.'
      : p.payment_status_label;
    const finished = ['success', 'refunded'].includes(p.payment_status);
    $('#payButton').disabled = ['creating', 'uncertain'].includes(p.invoice_state) || finished;
    $('#payButton').hidden = finished;
    $('#installmentField').hidden = !p.is_booking || finished;
    const input = $('#installmentAmount');
    const inputKey = `${p.id}:${p.paid_amount}:${openInvoice ? p.invoice_amount : 'new'}`;
    if (input.dataset.paymentKey !== inputKey) {
      input.value = openInvoice ? p.invoice_amount : p.minimum_payment;
      input.dataset.paymentKey = inputKey;
    }
    input.min = p.minimum_payment;
    input.max = p.payable_amount;
    input.disabled = openInvoice;
    $('#payRemaining').hidden = openInvoice;
    $('#installmentHint').textContent = openInvoice
      ? `Ochilgan to‘lov summasi: ${money(p.invoice_amount)}. Avval shu to‘lovni yakunlang.`
      : `Eng kam: ${money(p.minimum_payment)}. Eng ko‘p: ${money(p.payable_amount)}. Bron chegirmasi bir marta qo‘llanadi.`;
    $('#checkPayment').hidden = demo || !p.invoice_state;
    $('#payButton').textContent = p.checkout_url ? 'To‘lov sahifasini ochish' : 'To‘lovni amalga oshirish';
    $('#bookingQuestionnaire').hidden = !['partial', 'success'].includes(p.payment_status) || p.questionnaire_completed;
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

  function showPaymentResult(purchase, previousPaid = 0) {
    try { localStorage.removeItem('nmPaymentAttempt'); } catch (_) {}
      $('#paymentResultAmount').textContent = money(Number(purchase.paid_amount) - previousPaid);
      $('#paymentResultPaid').textContent = money(purchase.paid_amount);
      $('#paymentResultDebt').textContent = money(purchase.remaining_amount);
      $('#paymentResultNote').textContent = Number(purchase.remaining_amount) > 0 ? 'Bron qabul qilindi. Qolgan summani keyinroq to‘lashingiz mumkin.' : 'Kurs uchun to‘lov to‘liq yakunlandi.';
      showView('paymentResultView', 2);
  }

  function applyPayment(purchase) {
    replacePurchase(purchase);
    if (state.purchase?.id !== purchase.id) return;
    const previousPaid = Number(state.purchase.paid_amount || 0);
    state.purchase = purchase;
    if (['partial', 'success'].includes(purchase.payment_status) && Number(purchase.paid_amount) > previousPaid) {
      showPaymentResult(purchase, previousPaid);
      return;
    }
    if (purchase.payment_status === 'success') {
      if (!$('#paymentView').classList.contains('is-active')) return;
      if (purchase.questionnaire_completed) showSuccess(purchase);
      else { renderQuestionnaires(); showView('questionnaireView', 3); }
      toast('To‘lov muvaffaqiyatli qabul qilindi.');
    } else { renderPayment(); }
  }

  async function startPayment() {
    const purchaseId = state.purchase.id;
    if (state.purchase.is_booking && !$('#installmentAmount').reportValidity()) return;
    const payload = state.purchase.is_booking ? { amount: $('#installmentAmount').value, expected_paid: state.purchase.paid_amount } : {};
    try { localStorage.setItem('nmPaymentAttempt', JSON.stringify({ id: purchaseId, paid: state.purchase.paid_amount })); } catch (_) {}
    const button = $('#payButton'); button.disabled = true; button.textContent = 'To‘lov tayyorlanmoqda…';
    try {
      const endpoint = document.body.dataset.demo === '1' ? 'demo-payment' : 'payment';
      const data = await api(`/telegram-app/api/purchases/${purchaseId}/${endpoint}/`, { method: 'POST', body: JSON.stringify(payload) });
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
      state.purchase = data.purchase; replacePurchase(data.purchase); showSuccess(data.purchase);
    } catch (error) { toast(error.message, true); } finally { button.disabled = false; }
  }

  function resumePurchase(id) {
    state.purchase = state.purchases.find(item => item.id === id);
    if (state.purchase.payment_status === 'success') { renderQuestionnaires(); showView('questionnaireView', 3); }
    else if (!state.purchase.contract_accepted) { openContract(state.purchase); }
    else { renderPayment(); showView('paymentView', 2); }
  }

  function showSuccess(purchase) {
    $('#successDescription').textContent = purchase.payment_status === 'partial'
      ? `Bron va anketa qabul qilindi. Qolgan to‘lov: ${money(purchase.payable_amount)}. Bosh sahifadagi xarid orqali keyinroq to‘lashingiz mumkin.`
      : 'To‘lov va anketa qabul qilindi. Keyingi ma’lumotlarni Telegram orqali yuboramiz.';
    showView('successView', 3);
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
      else {
        let attempt;
        try { attempt = JSON.parse(localStorage.getItem('nmPaymentAttempt')); } catch (_) {}
        const purchase = attempt && state.purchases.find(item => item.id === attempt.id);
        if (purchase) {
          state.purchase = purchase;
          if (['partial', 'success'].includes(purchase.payment_status) && Number(purchase.paid_amount) > Number(attempt.paid)) showPaymentResult(purchase, Number(attempt.paid));
          else resumePurchase(purchase.id);
        }
      }
    } catch (error) {
      $('#courseList').innerHTML = `<article class="course-card"><h3>Ilovani ochib bo‘lmadi</h3><p>${escapeHtml(error.message)} Telegram bot ichidagi tugma orqali qayta urinib ko‘ring.</p></article>`;
      toast(error.message, true);
    }
  }

  $$('.segment__item').forEach(button => button.addEventListener('click', () => { state.type = button.dataset.purchaseType; $$('.segment__item').forEach(node => node.classList.toggle('is-active', node === button)); $('#familyArea').hidden = state.type !== 'family'; if (state.type === 'family' && !$$('.family-member').length) addFamilyMember(); updateTotal(); }));
  $('#paymentMode').addEventListener('change', updateTotal);
  $('#payRemaining').addEventListener('click', () => { $('#installmentAmount').value = state.purchase.payable_amount; });
  $('#bookingQuestionnaire').addEventListener('click', () => { renderQuestionnaires(); showView('questionnaireView', 3); });
  $('#continueAfterPayment').addEventListener('click', () => { if (state.purchase.questionnaire_completed) showSuccess(state.purchase); else { renderQuestionnaires(); showView('questionnaireView', 3); } });
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
