/* 首页视觉严格对齐「AI工艺_首页_最终版」；清单统一展示真实需求、图纸项目和技术工艺记录。 */
let homeItems = [], homeUser = {}, activeTab = 'mine', keyword = '', page = 1;
const PAGE_SIZE = 6;
// 首页附件在选择后立即以聊天附件卡片展示。FileList 不可直接修改，因此用
// 独立状态保存，并在移除单个文件时回写给对应 input。
let homeModelFiles = [];
let homeDocumentFiles = [];
let homeIndustry = 'semiconductor';
const HOME_ROLE_LABEL = {viewer:'只读用户',engineer:'工艺工程师',sales_manager:'销售经理',process_manager:'工艺技术经理',reviewer:'校核人员',process_director:'工艺技术总监',sales_director:'销售总监',finance_manager:'财务负责人',general_manager:'总经理/董事长',admin:'系统管理员'};
const HOME_NAV_ICONS = [
  '<svg class="cpq-nav-svg" width="16" height="16" viewBox="0 0 16 16" fill="none" xmlns="http://www.w3.org/2000/svg" aria-hidden="true" focusable="false"><path d="M0 0h16v16H0z"/><path fill="#8F9299" d="M14.481 7.5h-3.523A3.008 3.008 0 0 0 8.5 5.042V1.519A6.501 6.501 0 0 1 14.481 7.5Zm0 1A6.5 6.5 0 0 1 8.5 14.481v-3.523A3.008 3.008 0 0 0 10.958 8.5h3.523ZM1.52 8.5h3.523A3.008 3.008 0 0 0 7.5 10.958v3.523A6.5 6.5 0 0 1 1.519 8.5h.001Zm0-1A6.501 6.501 0 0 1 7.5 1.519v3.523A3.008 3.008 0 0 0 5.042 7.5H1.519h.001Z" data-follow-fill="#8F9299"/></svg>',
  '<svg class="cpq-nav-svg" width="16" height="16" viewBox="0 0 16 16" fill="none" xmlns="http://www.w3.org/2000/svg" aria-hidden="true" focusable="false"><path d="M0 0h16v16H0z"/><path fill-rule="evenodd" fill="#8F9299" d="M10.152.857a.487.487 0 0 1 .2.124l3.324 3.323a.487.487 0 0 1 .156.407v8.623c0 .253-.044.488-.134.704-.09.216-.224.414-.403.593a1.821 1.821 0 0 1-.592.402c-.216.09-.45.135-.704.135h-8c-.253 0-.488-.045-.704-.134a1.823 1.823 0 0 1-.592-.403 1.82 1.82 0 0 1-.403-.593 1.822 1.822 0 0 1-.134-.704V2.668c0-.254.044-.488.134-.704.09-.216.224-.414.403-.593A1.82 1.82 0 0 1 3.295.97c.216-.09.45-.135.704-.135h5.956a.49.49 0 0 1 .197.023Zm2.68 4.31v8.167c0 .278-.069.486-.208.625-.139.14-.347.209-.625.209h-8c-.278 0-.486-.07-.625-.209-.139-.139-.208-.347-.208-.625V2.668c0-.278.07-.487.208-.625.139-.14.347-.209.625-.209h5.5v2.167c0 .161.028.31.085.448.057.137.143.263.257.377.114.114.24.2.377.256.137.057.287.086.448.086h2.166ZM10.5 4.002v-1.46l1.626 1.627h-1.46c-.055 0-.096-.014-.124-.042-.028-.028-.042-.07-.042-.125Zm-.132 3.317H5.634a.491.491 0 0 1-.5-.5.49.49 0 0 1 .277-.45.488.488 0 0 1 .223-.05h4.733a.492.492 0 0 1 .5.5.491.491 0 0 1-.5.5ZM5.634 9.684h4.733a.491.491 0 0 0 .5-.5.49.49 0 0 0-.278-.449.488.488 0 0 0-.222-.05H5.634a.492.492 0 0 0-.5.5.491.491 0 0 0 .5.5Z" data-follow-fill="#8F9299"/></svg>',
  '<svg class="cpq-nav-svg" width="16" height="16" viewBox="0 0 16 16" fill="none" xmlns="http://www.w3.org/2000/svg" aria-hidden="true" focusable="false"><path d="M0 0h16v16H0z"/><path fill-rule="evenodd" fill="#8F9299" d="M10.352.981a.486.486 0 0 0-.398-.147H4c-.253 0-.488.045-.704.135a1.82 1.82 0 0 0-.592.402c-.18.18-.314.377-.403.593a1.82 1.82 0 0 0-.134.704v10.666c0 .254.044.488.134.704.09.216.224.414.403.593.178.179.376.313.592.403.216.089.45.134.704.134h8c.253 0 .488-.045.704-.134.216-.09.413-.224.592-.403.18-.18.313-.377.403-.593.09-.216.134-.45.134-.704V4.711a.491.491 0 0 0-.156-.407L10.352.981Zm.147 1.56v1.46c0 .056.014.097.041.125.028.028.07.042.126.042h1.459l-1.626-1.626Zm2.333 6.963V5.168h-2.167c-.16 0-.31-.029-.447-.086a1.159 1.159 0 0 1-.377-.256 1.16 1.16 0 0 1-.257-.377 1.157 1.157 0 0 1-.085-.448V1.834h-5.5c-.278 0-.486.07-.625.209-.139.139-.208.347-.208.625v10.666c0 .278.069.486.208.625.139.14.347.209.625.209h8c.278 0 .486-.07.625-.209.139-.139.208-.347.208-.625v-3.83ZM8.5 6.277v1.867h1.867a.492.492 0 0 1 .5.5.491.491 0 0 1-.5.5H8.5v1.866a.49.49 0 0 1-.277.45.49.49 0 0 1-.223.05.49.49 0 0 1-.45-.277.49.49 0 0 1-.05-.223V9.144H5.634a.491.491 0 0 1-.5-.5.49.49 0 0 1 .277-.45.488.488 0 0 1 .223-.05H7.5V6.277a.49.49 0 0 1 .5-.5.49.49 0 0 1 .5.5Z" data-follow-fill="#8F9299"/></svg>',
  '<svg class="cpq-nav-svg" width="16" height="16" viewBox="0 0 16 16" fill="none" xmlns="http://www.w3.org/2000/svg" aria-hidden="true" focusable="false"><path d="M0 0h16v16H0z"/><path fill-rule="evenodd" fill="#8F9299" d="M15.167 8c0 .99-.175 1.907-.525 2.751a7.118 7.118 0 0 1-1.574 2.317c-.7.7-1.472 1.224-2.317 1.574A7.12 7.12 0 0 1 8 15.167c-.99 0-1.907-.175-2.751-.525a7.122 7.122 0 0 1-2.317-1.574 7.118 7.118 0 0 1-1.574-2.317 7.116 7.116 0 0 1-.525-2.75c0-.99.175-1.907.525-2.752a7.117 7.117 0 0 1 1.574-2.316c.7-.7 1.472-1.225 2.317-1.575a7.118 7.118 0 0 1 2.75-.525c.99 0 1.907.175 2.752.525.845.35 1.617.875 2.317 1.575.7.7 1.224 1.471 1.574 2.316.35.845.525 1.762.525 2.751Zm-1 0c0-.851-.15-1.64-.452-2.367A6.126 6.126 0 0 0 12.36 3.64a6.124 6.124 0 0 0-1.993-1.355A6.125 6.125 0 0 0 8 1.833c-.852 0-1.64.151-2.367.452A6.124 6.124 0 0 0 3.639 3.64a6.124 6.124 0 0 0-1.354 1.993A6.126 6.126 0 0 0 1.833 8c0 .852.15 1.64.452 2.367a6.125 6.125 0 0 0 1.354 1.994 6.124 6.124 0 0 0 1.994 1.354A6.123 6.123 0 0 0 8 14.167c.851 0 1.64-.15 2.367-.452a6.124 6.124 0 0 0 1.993-1.354 6.124 6.124 0 0 0 1.355-1.994A6.125 6.125 0 0 0 14.167 8ZM8.4 8.244h3.5a.5.5 0 0 1 0 1h-4a.5.5 0 0 1-.5-.5v-4.5a.5.5 0 0 1 1 0v4Z" data-follow-fill="#8F9299"/></svg>',
  '<svg class="cpq-nav-svg" width="16" height="16" viewBox="0 0 16 16" fill="none" xmlns="http://www.w3.org/2000/svg" aria-hidden="true" focusable="false"><path d="M0 0h16v16H0z"/><path fill-rule="evenodd" fill="#8F9299" d="M12.654 14.497v.003h-10a1.82 1.82 0 0 1-.704-.134 1.82 1.82 0 0 1-.592-.403 1.823 1.823 0 0 1-.403-.593 1.82 1.82 0 0 1-.134-.703V3.333c0-.253.044-.487.134-.703.09-.216.224-.414.402-.593.18-.18.377-.313.593-.403.216-.09.45-.134.704-.134h2.709c.286 0 .546.055.781.165.235.11.444.275.627.495l1.067 1.28c.017.02.036.035.057.045.022.01.045.015.071.015h4.688c.253 0 .488.045.704.134.216.09.413.224.592.403.18.179.314.377.403.593.09.216.134.45.134.703V7.23a2 2 0 0 1 .272.275c.184.224.307.463.37.716.064.254.067.523.01.806l-.8 4c-.043.214-.117.41-.223.586a1.822 1.822 0 0 1-.412.473c-.17.138-.35.242-.544.311-.16.057-.329.09-.506.1ZM1.821 11.09V3.333c0-.278.069-.486.208-.625.139-.139.347-.208.625-.208h2.709c.13 0 .248.025.355.075.107.05.202.125.285.225L7.07 4.08c.117.14.25.245.399.315.15.07.315.105.497.105h4.688c.278 0 .486.07.625.208.139.14.208.348.208.625v1.505a2.13 2.13 0 0 0-.146-.005H4.29c-.481 0-.87.117-1.168.351-.297.234-.502.586-.614 1.054L1.82 11.09Zm12.338-2.26a.826.826 0 0 0-.005-.366.828.828 0 0 0-.168-.326.827.827 0 0 0-.286-.228.828.828 0 0 0-.358-.077H4.288c-.219 0-.396.054-.53.16-.136.106-.229.266-.28.479l-.962 4a.828.828 0 0 0-.009.374c.026.118.08.23.164.336.084.106.18.186.289.239.11.053.231.079.366.079h9.214c.228 0 .41-.056.546-.168.137-.111.227-.279.272-.502l.8-4Z" data-follow-fill="#8F9299"/></svg>',
  '<svg class="cpq-nav-svg" width="16" height="16" viewBox="0 0 16 16" fill="none" xmlns="http://www.w3.org/2000/svg" aria-hidden="true" focusable="false"><path d="M0 0h16v16H0z"/><path fill-rule="evenodd" fill="#8F9299" d="m14.624 13.921-1.952-1.952c.692-.845 1.154-1.744 1.388-2.7.15-.613.205-1.25.167-1.91-.1-1.686-.79-3.15-2.07-4.389-.826-.729-1.716-1.228-2.67-1.498a6.429 6.429 0 0 0-1.877-.236 6.395 6.395 0 0 0-1.883.313c-.935.306-1.797.837-2.585 1.593-.756.788-1.287 1.65-1.594 2.585a6.394 6.394 0 0 0-.312 1.883 6.43 6.43 0 0 0 .236 1.876c.27.955.769 1.845 1.498 2.67.744.769 1.568 1.325 2.473 1.668a6.437 6.437 0 0 0 1.917.402c1.687.1 3.224-.419 4.609-1.554l1.952 1.953c.104.093.221.14.352.14a.47.47 0 0 0 .344-.148.473.473 0 0 0 .148-.344.513.513 0 0 0-.14-.352Zm-4.268-1.346c-.77.413-1.643.633-2.62.659-1.563-.042-2.86-.578-3.89-1.61a5.413 5.413 0 0 1-.95-1.269c-.414-.77-.633-1.642-.66-2.62.042-1.562.579-2.858 1.61-3.89a5.412 5.412 0 0 1 1.27-.95c.768-.413 1.642-.632 2.62-.658 1.561.042 2.858.578 3.89 1.609.385.386.702.809.95 1.27.413.769.632 1.642.658 2.62-.042 1.561-.578 2.858-1.609 3.889a5.413 5.413 0 0 1-1.27.95Z" data-follow-fill="#8F9299"/></svg>',
  '<svg class="cpq-nav-svg" width="16" height="16" viewBox="0 0 16 16" fill="none" xmlns="http://www.w3.org/2000/svg" aria-hidden="true" focusable="false"><path d="M0 0h16v16H0z"/><path fill-rule="evenodd" fill="#8F9299" d="m12.007 13.178 2.55-4.333c.165-.282.248-.563.248-.845 0-.282-.083-.563-.249-.845l-2.549-4.333a1.655 1.655 0 0 0-.6-.617A1.653 1.653 0 0 0 10.57 2H5.429c-.317 0-.596.068-.835.205-.24.137-.44.343-.601.617l-2.55 4.333A1.654 1.654 0 0 0 1.196 8c0 .282.083.563.249.845l2.549 4.333c.16.274.361.48.6.617.24.137.519.205.836.205h5.142c.318 0 .596-.068.836-.205.24-.137.44-.343.6-.617ZM14.131 8c0 .169-.05.338-.15.507l-2.548 4.333a.996.996 0 0 1-.36.37.994.994 0 0 1-.502.123H5.429a.994.994 0 0 1-.501-.123.995.995 0 0 1-.36-.37l-2.55-4.333A.993.993 0 0 1 1.87 8c0-.169.05-.338.15-.507L4.566 3.16a.995.995 0 0 1 .36-.37.994.994 0 0 1 .502-.123h5.142c.19 0 .358.04.501.123a.995.995 0 0 1 .36.37l2.55 4.333c.1.17.149.338.149.507Z" data-follow-fill="#8F9299"/><path fill-rule="evenodd" fill="#8F9299" d="m14.27 8.676-2.55 4.333c-.24.407-.677.657-1.15.657H5.43c-.473 0-.91-.25-1.15-.657L1.731 8.676a1.333 1.333 0 0 1 0-1.352l2.55-4.334c.239-.407.676-.657 1.148-.657h5.142c.473 0 .91.25 1.15.657l2.548 4.334c.245.417.245.935 0 1.352Zm-4.416.092C9.95 8.532 10 8.276 10 8s-.049-.532-.146-.768a1.986 1.986 0 0 0-.44-.646 1.989 1.989 0 0 0-.646-.44A1.986 1.986 0 0 0 8 6c-.276 0-.532.049-.768.146a1.989 1.989 0 0 0-.646.44c-.196.195-.342.41-.44.646A1.987 1.987 0 0 0 6 8c0 .276.049.532.146.768.098.235.245.451.44.646s.41.342.646.44c.236.097.492.146.768.146s.532-.049.768-.146c.235-.098.451-.245.646-.44.196-.195.342-.41.44-.646Z" data-follow-fill="#8F9299"/><path fill-rule="evenodd" fill="#8F9299" d="m14.27 8.676-2.55 4.333c-.24.407-.677.657-1.15.657H5.43c-.473 0-.91-.25-1.15-.657L1.731 8.676a1.333 1.333 0 0 1 0-1.352l2.55-4.334c.239-.407.676-.657 1.148-.657h5.142c.473 0 .91.04 1.15.657l2.548 4.334c.245.417.245.935 0 1.352Zm-4.416.092C9.95 8.532 10 8.276 10 8s-.049-.532-.146-.768a1.986 1.986 0 0 0-.44-.646 1.989 1.989 0 0 0-.646-.44A1.986 1.986 0 0 0 8 6c-.276 0-.532.049-.768.146a1.989 1.989 0 0 0-.646.44c-.196.195-.342.41-.44.646A1.987 1.987 0 0 0 6 8c0 .276.049.532.146.768.098.235.245.451.44.646s.41.342.646.44c.236.097.492.146.768.146s.532-.049.768-.146c.235-.098-.451-.44-.646-.646Z" data-follow-fill="#8F9299"/></svg>',
  '<svg class="cpq-nav-svg" width="16" height="16" viewBox="0 0 16 16" fill="none" xmlns="http://www.w3.org/2000/svg" aria-hidden="true" focusable="false"><path d="M0 0h16v16H0z"/><path fill-rule="evenodd" fill="#8F9299" d="M11 4.667c0 .414-.073.798-.22 1.151a2.982 2.982 0 0 1-.659.97 2.98 2.98 0 0 1-.97.659c-.353.147-.737.22-1.15.22-.415 0-.799-.073-1.152-.22a2.978 2.978 0 0 1-.97-.659 2.98 2.98 0 0 1-.66-.97A2.979 2.979 0 0 1 5 4.667c0-.415.073-.798.22-1.152a2.98 2.98 0 0 1 .659-.97 2.98 2.98 0 0 1 .97-.659c.353-.146.737-.22 1.151-.22.414 0 .798.074 1.152.22.353.147.676.366.97.66.292.292.512.616.658.97.147.353.22.736.22 1.15Zm-8.6 9.666h11.2a.727.727 0 0 0 .282-.053.725.725 0 0 0 .237-.161.73.73 0 0 0 .16-.237.729.729 0 0 0 .054-.282c0-.88-.012-1.473-.037-1.779-.04-.488-.136-.883-.29-1.183a2.991 2.991 0 0 0-.549-.762 2.984 2.984 0 0 0-.762-.55c-.3-.152-.695-.249-1.183-.289C11.206 9.012 10.614 9 9.733 9H6.267c-.88 0-1.473.012-1.779.037-.488.04-.883.137-1.183.29-.29.147-.544.33-.762.549a2.984 2.984 0 0 0-.55.762c-.152.3-.249.695-.289 1.183-.025.306-.037.899-.037 1.78a.73.73 0 0 0 .053.28c.036.087.09.166.162.238a.727.727 0 0 0 .518.215Z" data-follow-fill="#8F9299"/></svg>'
];
// 第七个图标的内圈路径在原 SVG 中重复出现一次；保留一次即可得到相同视觉结果，并避免重复绘制。
HOME_NAV_ICONS[6] = '<svg class="cpq-nav-svg" width="16" height="16" viewBox="0 0 16 16" fill="none" xmlns="http://www.w3.org/2000/svg" aria-hidden="true" focusable="false"><path d="M0 0h16v16H0z"/><path fill-rule="evenodd" fill="#8F9299" d="m12.007 13.178 2.55-4.333c.165-.282.248-.563.248-.845 0-.282-.083-.563-.249-.845l-2.549-4.333a1.655 1.655 0 0 0-.6-.617A1.653 1.653 0 0 0 10.57 2H5.429c-.317 0-.596.068-.835.205-.24.137-.44.343-.601.617l-2.55 4.333A1.654 1.654 0 0 0 1.196 8c0 .282.083.563.249.845l2.549 4.333c.16.274.361.48.6.617.24.137.519.205.836.205h5.142c.318 0 .596-.068.836-.205.24-.137.44-.343.6-.617ZM14.131 8c0 .169-.05.338-.15.507l-2.548 4.333a.996.996 0 0 1-.36.37.994.994 0 0 1-.502.123H5.429a.994.994 0 0 1-.501-.123.995.995 0 0 1-.36-.37l-2.55-4.333A.993.993 0 0 1 1.87 8c0-.169.05-.338.15-.507L4.566 3.16a.995.995 0 0 1 .36-.37.994.994 0 0 1 .502-.123h5.142c.19 0 .358.04.501.123a.995.995 0 0 1 .36.37l2.55 4.333c.1.17.149.338.149.507Z" data-follow-fill="#8F9299"/><path fill-rule="evenodd" fill="#8F9299" d="m14.27 8.676-2.55 4.333c-.24.407-.677.657-1.15.657H5.43c-.473 0-.91-.25-1.15-.657L1.731 8.676a1.333 1.333 0 0 1 0-1.352l2.55-4.334c.239-.407.676-.657 1.148-.657h5.142c.473 0 .91.25 1.15.657l2.548 4.334c.245.417.245.935 0 1.352Zm-4.416.092C9.95 8.532 10 8.276 10 8s-.049-.532-.146-.768a1.986 1.986 0 0 0-.44-.646 1.989 1.989 0 0 0-.646-.44A1.986 1.986 0 0 0 8 6c-.276 0-.532.049-.768.146a1.989 1.989 0 0 0-.646.44c-.196.195-.342.41-.44.646A1.987 1.987 0 0 0 6 8c0 .276.049.532.146.768.098.235.245.451.44.646s.41.342.646.44c.236.097.492.146.768.146s.532-.049.768-.146c.235-.098.451-.245.646-.44Z" data-follow-fill="#8F9299"/></svg>';
HOME_NAV_ICONS[0] = HOME_NAV_ICONS[0].replace(/#8F9299/g, '#FFFFFF');
for (let i = 1; i < HOME_NAV_ICONS.length; i += 1) {
  HOME_NAV_ICONS[i] = HOME_NAV_ICONS[i].replace(/#8F9299/g, '#374151');
}
function homeNavIcon(index) { return HOME_NAV_ICONS[index] || ''; }
// 当前“我的清单”保留全部已创建需求单，并额外纳入用户指定的图纸项目。
const HOME_MINE_CREATED_AT = '2026-07-14 11:08:35';

function homeEsc(v) { return String(v ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])); }
function homeToast(message, error = false) { const el=document.createElement('div'); el.className=`home-toast${error?' error':''}`; el.textContent=message; document.body.append(el); setTimeout(()=>el.remove(),3200); }
function homeStatus(status) {
  return ({draft:['待提交','status-submitted'],pending_confirmation:['待确认','status-confirm'],pending_review:['待审核','status-review'],approved:['已通过','status-completed'],published:['已完成','status-completed'],rejected:['已退回','status-review'],uploaded:['待图纸解析','status-submitted'],parsed:['图纸已解析','status-confirm'],tech_record:['技术工艺已录入','status-completed']})[status] || ['处理中','status-submitted'];
}
function homeProjectStatus(project = {}, record = null) {
  if (record) return 'tech_record';
  if (project.has_ir || project.stages?.parsed) return 'parsed';
  return 'uploaded';
}
function homeVirtualRequirement(project = {}, record = null) {
  const projectId = project.project_id || record?.project_id || '';
  return {
    project_id: projectId,
    requirement_no: record?.code || `PRJ-${projectId}`,
    title: record?.name || project.project_name || project.device_name || project.source_filename || '未命名图纸项目',
    status: homeProjectStatus(project, record),
    created_by: record?.owner || project.owner || 'system',
    created_at: project.created_at || record?.created_at || '',
    data: { customer_project: project.device_name || '' },
  };
}
function mergeHomeItems(requirementItems = [], projects = [], records = []) {
  const projectById = new Map(projects.filter(p => p?.project_id).map(p => [p.project_id, p]));
  const requirementById = new Map(requirementItems
    .filter(item => item?.project?.project_id || item?.requirement?.project_id)
    .map(item => [item.project?.project_id || item.requirement?.project_id, item]));
  const recordByProject = new Map(records.filter(record => record?.project_id).map(record => [record.project_id, record]));
  const projectIds = new Set([...projectById.keys(), ...requirementById.keys(), ...recordByProject.keys()]);
  return [...projectIds].map(projectId => {
    const saved = requirementById.get(projectId);
    const record = recordByProject.get(projectId) || null;
    const project = projectById.get(projectId) || saved?.project || {
      project_id: projectId, owner: record?.owner || 'system', created_at: record?.created_at || '', source_filename: '', stages: {},
    };
    return {
      project,
      requirement: saved?.requirement || homeVirtualRequirement(project, record),
      hasRequirement: Boolean(saved?.requirement),
      techRecord: record,
    };
  }).sort((a, b) => String(b.project.created_at || b.requirement.created_at || '').localeCompare(String(a.project.created_at || a.requirement.created_at || '')));
}
function homeOwner(item) { return item?.project?.owner || item?.requirement?.created_by || 'system'; }
function homeOwnerName(item) { return item?.project?.owner_display_name || homeOwner(item); }
function homeIsMine(item) { return Boolean(homeUser?.username) && homeOwner(item) === homeUser.username; }
function homeCanManage(item) {
  const role = homeUser?.role;
  return role === 'admin' || role === 'process_manager' || (role === 'engineer' && homeOwner(item) === homeUser.username);
}
function homeCacheKey(projectId) { return `cad_engine:last_page:${projectId}`; }
function homeCachedTarget(projectId) {
  const saved = localStorage.getItem(homeCacheKey(projectId));
  if (!saved) return '';
  try {
    const url = new URL(saved, location.origin);
    if (url.origin !== location.origin || url.searchParams.get('project') !== projectId || url.pathname.endsWith('/home.html')) return '';
    // 2.2–2.6 是连续的技术工艺子步骤。首页恢复项目时统一回到 2.1，
    // 避免脱离图纸解析上下文直接进入中间步骤。
    if (url.pathname.startsWith('/apps/tech-process/')) return homeProjectPage('index.html', projectId);
    return `${url.pathname}${url.search}${url.hash}`;
  } catch { return ''; }
}
function homeTarget(item) {
  const projectId = item.project?.project_id;
  if (!projectId) return 'home.html';
  return homeCachedTarget(projectId) || `requirement-detail.html?project=${encodeURIComponent(projectId)}`;
}
function homeStepHas(value) {
  if (Array.isArray(value)) return value.some(homeStepHas);
  if (value && typeof value === 'object') return Object.entries(value).some(([key, item]) => !['project_id','updated_at','timing','confirmed','confirmed_by','confirmed_at'].includes(key) && homeStepHas(item));
  return typeof value === 'string' ? Boolean(value.trim()) : value !== null && value !== undefined && value !== false;
}
function homeProjectPage(page, projectId) { return `${page}?project=${encodeURIComponent(projectId)}`; }
function homeTechPage(projectId, step) { return `/apps/tech-process/?biz=tech&project=${encodeURIComponent(projectId)}&step=${step}`; }
async function homeCurrentTarget(item) {
  const projectId = item.project?.project_id;
  const cached = homeCachedTarget(projectId);
  if (cached) return cached;
  const [flow, projectData, aggregate] = await Promise.all([
    api(`/api/projects/${encodeURIComponent(projectId)}/workflow`),
    api(`/api/projects/${encodeURIComponent(projectId)}`),
    api(`/api/projects/${encodeURIComponent(projectId)}/summary`).catch(() => ({steps:{}})),
  ]);
  const requirement = flow.requirement || item.requirement || null;
  const status = requirement?.status || '';
  if (['draft','rejected'].includes(status)) return homeProjectPage('requirement-create.html', projectId);
  if (status === 'pending_confirmation') return homeProjectPage('requirement-confirm.html', projectId);
  if (status === 'pending_review') return homeProjectPage('requirement-review.html', projectId);
  const report = flow.report || null;
  if (report) {
    if (report.status === 'in_review') return homeProjectPage('report-review.html', projectId);
    if (['approved','published'].includes(report.status)) return homeProjectPage('report-publish.html', projectId);
    return homeProjectPage('summary.html', projectId);
  }
  const hasIr = Boolean(projectData.ir?.parts?.length || projectData.meta?.has_ir || item.project?.has_ir || item.project?.stages?.parsed);
  if (!hasIr) return homeProjectPage('index.html', projectId);
  // 即便 2.2–2.6 已有保存结果，也不从首页直达这些子页；统一先进入 2.1。
  return homeProjectPage('index.html', projectId);
}
async function homeOpenProject(item, card) {
  const projectId = item.project?.project_id;
  if (!projectId) return;
  card?.classList.add('is-opening');
  try { location.href = await homeCurrentTarget(item); }
  catch (error) { homeToast(`无法定位当前步骤：${error.message}，已打开流程详情。`, true); location.href = homeTarget(item); }
}
function homeFiltered() {
  const term=keyword.trim().toLowerCase();
  return homeItems.filter((item)=>{
    const {requirement:r={},project:p={},techRecord}=item;
    const record=techRecord||{};
    const mine=activeTab !== 'mine' || homeIsMine(item);
    const hay=[r.requirement_no,r.title,r.created_by,p.owner,p.owner_display_name,p.project_name,p.source_filename,p.device_name,r.data?.customer_project,r.created_at,record.code,record.name].join(' ').toLowerCase();
    return mine && (!term || hay.includes(term));
  });
}
function renderCards() {
  const all=homeFiltered(), max=Math.max(1,Math.ceil(all.length/PAGE_SIZE)); page=Math.min(page,max);
  const rows=all.slice((page-1)*PAGE_SIZE,page*PAGE_SIZE);
  const grid=document.querySelector('#cardGrid'), info=document.querySelector('#paginationInfo'), controls=document.querySelector('#paginationControls');
  grid.innerHTML=rows.length ? rows.map((item)=>{
    const {requirement:r={},project:p={}}=item;
    const [label,clazz]=homeStatus(r.status), who=homeOwnerName(item), initial=who.slice(0,1).toUpperCase();
    const title=p.project_name||r.title||r.data?.customer_project||p.device_name||p.source_filename||'未命名工艺需求';
    const action=homeCanManage(item)
      ? `<button class="request-edit" type="button" data-edit-project="${homeEsc(p.project_id||'')}">编辑</button>`
      : '<span class="request-readonly">只读</span>';
    return `<article class="request-card" data-target="${homeEsc(homeTarget(item))}" data-project-id="${homeEsc(p.project_id||'')}"><div class="request-card-header"><span class="request-id">${homeEsc(r.requirement_no||`PRJ-${p.project_id||''}`)}</span><span class="request-status ${clazz}">${label}</span></div><div class="request-title" title="${homeEsc(title)}">${homeEsc(title)}</div><div class="request-footer"><div class="request-creator"><span class="creator-avatar">${homeEsc(initial)}</span><span>${homeEsc(who)} · ${homeEsc((r.created_at||p.created_at||'').slice(0,10)||'—')}</span></div>${action}</div></article>`;
  }).join('') : '<div class="empty-grid">暂无匹配的真实工艺需求。上传图纸后可在此查看处理进度。</div>';
  grid.querySelectorAll('[data-target]').forEach(el=>el.onclick=()=>homeOpenProject(homeItems.find(item=>item.project?.project_id===el.dataset.projectId),el));
  grid.querySelectorAll('[data-edit-project]').forEach(button => button.onclick = event => {
    event.stopPropagation();
    homeOpenProjectEditor(homeItems.find(item => item.project?.project_id === button.dataset.editProject));
  });
  const start=all.length ? (page-1)*PAGE_SIZE+1 : 0, end=Math.min(page*PAGE_SIZE,all.length);
  info.textContent=`共 ${all.length} 个结果，当前显示 ${start}–${end}`;
  controls.innerHTML=homePages(max).map(n=>n==='…'?'<button class="page-btn" disabled>…</button>':`<button class="page-btn ${n===page?'active':''}" data-page="${n}">${n}</button>`).join('');
  controls.querySelectorAll('[data-page]').forEach(el=>el.onclick=()=>{page=Number(el.dataset.page);renderCards();});
}
function homePages(max) { if(max<=5)return Array.from({length:max},(_,i)=>i+1); return page<=3?[1,2,3,'…',max]:page>=max-2?[1,'…',max-2,max-1,max]:[1,'…',page,'…',max]; }
function homeOpenProjectEditor(item) {
  if (!item?.project?.project_id || !homeCanManage(item)) {
    homeToast('你没有编辑此项目的权限。', true);
    return;
  }
  document.querySelector('#projectEditorModal')?.remove();
  const projectId = item.project.project_id;
  const initialName = item.project.project_name || item.requirement?.title || item.project.device_name || item.project.source_filename || '';
  document.body.insertAdjacentHTML('beforeend', `<div class="project-editor-mask" id="projectEditorModal" role="dialog" aria-modal="true" aria-labelledby="projectEditorTitle"><form class="project-editor"><div class="project-editor-head"><h2 id="projectEditorTitle">编辑项目</h2><button class="project-editor-close" type="button" aria-label="关闭">×</button></div><label>项目名称<input id="projectEditorName" maxlength="120" required value="${homeEsc(initialName)}" placeholder="请输入项目名称"></label><p class="project-editor-note">原始图纸、工艺结果和处理记录不会被修改。</p><div class="project-editor-actions"><button class="project-delete" type="button">删除项目</button><span></span><button class="project-cancel" type="button">取消</button><button class="project-save" type="submit">保存修改</button></div></form></div>`);
  const modal = document.querySelector('#projectEditorModal');
  const close = () => modal.remove();
  modal.querySelector('.project-editor-close').onclick = close;
  modal.querySelector('.project-cancel').onclick = close;
  modal.onclick = event => { if (event.target === modal) close(); };
  modal.querySelector('form').onsubmit = async event => {
    event.preventDefault();
    const name = modal.querySelector('#projectEditorName').value.trim();
    const save = modal.querySelector('.project-save');
    if (!name) return;
    save.disabled = true;
    try {
      await api(`/api/projects/${encodeURIComponent(projectId)}/management`, {method:'PATCH', body:JSON.stringify({name})});
      item.project.project_name = name;
      if (item.requirement) item.requirement.title = name;
      renderCards();
      close();
      homeToast('项目名称已更新。');
    } catch (error) { homeToast(error.message || '保存项目失败', true); save.disabled = false; }
  };
  modal.querySelector('.project-delete').onclick = async () => {
    if (!confirm(`确认删除项目“${initialName}”？删除后将不再出现在清单中。`)) return;
    const del = modal.querySelector('.project-delete');
    del.disabled = true;
    try {
      await api(`/api/projects/${encodeURIComponent(projectId)}/management`, {method:'DELETE'});
      homeItems = homeItems.filter(row => row.project?.project_id !== projectId);
      renderCards();
      close();
      homeToast('项目已删除。');
    } catch (error) { homeToast(error.message || '删除项目失败', true); del.disabled = false; }
  };
  modal.querySelector('#projectEditorName').focus();
}
// 首页使用左侧工作导航，主体内容由此统一渲染。
function renderHomeBase() {
  document.querySelector('#app').innerHTML=`<div class="home-shell">
    <aside class="home-nav" id="homeNav">
      <div class="home-nav-rail">
        <button class="home-nav-toggle" id="homeNavToggle" aria-label="展开导航" aria-expanded="false">☰</button>
        <button class="home-nav-icon active" data-home-view="mine" title="我的清单">▣</button>
        <button class="home-nav-icon" data-home-view="all" title="全部清单">▤</button>
        <button class="home-nav-icon" id="homeNavCreate" title="新建需求">＋</button><span class="home-nav-spacer"></span>
        <a class="home-nav-icon" href="account.html" title="账户设置">⚙</a>
      </div>
      <div class="home-nav-panel">
        <div class="home-nav-panel-head"><strong>工作台</strong><button class="home-nav-close" id="homeNavClose" aria-label="收起导航">×</button></div>
        <button class="home-nav-create" id="homeNavCreatePanel">＋ 创建工艺评估需求</button>
        <label class="home-nav-search"><span>⌕</span><input id="sideSearchInput" placeholder="搜索需求单"></label>
        <div class="home-nav-group"><span>清单</span><button class="home-nav-item active" data-home-view="mine">我的清单</button><button class="home-nav-item" data-home-view="all">全部清单</button></div>
        <div class="home-nav-group home-nav-recent"><span>最近项目</span><div id="sideProjectList"><p class="home-nav-empty">正在加载…</p></div></div>
        <div class="home-nav-account"><div class="home-nav-account-avatar" id="sideUserAvatar">AI</div><div><b id="sideUserName">加载中…</b><small id="sideUserRole">—</small></div><a href="account.html" title="账户设置">›</a></div>
        <div class="home-nav-account-actions"><a href="account.html">账户设置</a><a href="account.html#users" id="sideUserAdminLink" hidden>用户与权限</a><button type="button" id="homeLogout">退出登录</button></div>
      </div>
    </aside>
    <main class="page-container home-main"><section class="chat-container">
      <div class="greeting"><h1 class="greeting-title"><span class="highlight">创作无限可能</span>，AI 工艺</h1><p class="greeting-subtitle">今天我能帮你做些什么？</p></div>
      <div class="category-tags"><button class="category-tag active" data-category="ai">⚡ AI工艺</button><button class="category-tag" data-category="hot">🔥 热门问答</button><button class="category-tag" data-category="wiki">📚 企业百科</button></div>
      <section class="unified-input-card"><div class="unified-content"><p class="unified-intro">请使用 @ 引用需求编号，或直接上传文件内容（图纸、文档等），开始解析工艺、生成方案。</p><div class="format-row"><span class="format-label">支持格式：</span><span class="format-label">3D 格式</span><div class="format-tags-row"><span class="format-tag-inline">STEP</span><span class="format-tag-inline">STP</span><span class="format-tag-inline">SLDPRT</span><span class="format-tag-inline">STL</span><span class="format-tag-inline">SAT</span></div><span class="format-label">2D 格式</span><div class="format-tags-row"><span class="format-tag-inline">DWG</span><span class="format-tag-inline">DXF</span><span class="format-tag-inline">PDF</span></div></div></div><div class="upload-names" id="uploadNames" aria-live="polite"></div><textarea id="homePrompt" class="unified-textarea" maxlength="200" placeholder="请输入你的问题或需求描述..." rows="3"></textarea><div class="unified-input-area"><label class="upload-section" for="modelFile"><input id="modelFile" class="upload-input" type="file" accept="image/*,.pdf,.dwg,.dxf,.step,.stp,.sldprt,.stl,.sat"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="17 8 12 3 7 8"/><line x1="12" y1="3" x2="12" y2="15"/></svg><span>上传模型图纸</span></label><label class="upload-section" for="documentFiles"><input id="documentFiles" class="upload-input" type="file" multiple accept="image/*,.pdf,.txt,.md,.csv,.doc,.docx"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/></svg><span>上传需求文档</span></label><span class="char-counter" id="charCounter">0/200</span><button class="send-btn" id="sendBtn" aria-label="创建需求"><svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="22" y1="2" x2="11" y2="13"/><polygon points="22 2 15 22 11 13 2 9 22 2"/></svg></button></div></section>
      <section class="list-section"><div class="list-header"><div class="list-tabs"><button class="list-tab active" data-list="mine">我的清单</button><button class="list-tab" data-list="all">全部清单</button></div><div class="search-box"><input class="search-input" id="searchInput" placeholder="支持通过客户名&创建人搜索"><button class="search-btn" id="searchBtn">查询</button></div></div><div class="card-grid" id="cardGrid"></div><div class="pagination"><div class="pagination-info" id="paginationInfo"></div><div class="pagination-controls" id="paginationControls"></div></div></section>
    </section></main></div>`;
}

function renderHome() {
  renderHomeBase();
  const nav = document.querySelector('#homeNav');
  nav.innerHTML=`<div class="cpq-nav-collapsed" id="homeNavCollapsed">
    <button class="cpq-nav-logo" id="homeNavToggle" aria-label="展开侧栏" aria-expanded="false">${homeNavIcon(0)}</button>
    <button class="cpq-nav-icon" id="homeNavSearch">${homeNavIcon(5)}<b>搜索</b></button>
    <button class="cpq-nav-icon" id="homeNavCreate">${homeNavIcon(2)}<b>新建清单</b></button>
    <button class="cpq-nav-icon active" data-home-view="mine">${homeNavIcon(1)}<b>我的清单</b></button>
    <button class="cpq-nav-icon" data-home-view="all">${homeNavIcon(4)}<b>全部清单</b></button>
    <i class="cpq-nav-divider"></i>
    <button class="cpq-nav-icon" id="homeNavHistory">${homeNavIcon(3)}<b>历史对话</b></button>
    <i class="cpq-nav-grow"></i>
    <div class="cpq-nav-bottom-icons">
      <button class="cpq-nav-icon" id="homeNavSettings">${homeNavIcon(6)}<b>模型设置</b></button>
      <a class="cpq-nav-icon" href="account.html">${homeNavIcon(7)}<b>用户设置</b></a>
    </div>
  </div>
  <div class="cpq-nav-expanded" id="homeNavExpanded">
    <div class="cpq-nav-brand-row"><button class="cpq-nav-brand" id="homeNavBrandToggle" aria-label="收起侧栏" aria-expanded="true"><span class="cpq-nav-brand-icon">${homeNavIcon(0)}</span><strong>AI工艺平台</strong></button></div>
    <div class="cpq-nav-search"><label><span class="cpq-nav-search-icon" aria-hidden="true">${homeNavIcon(5)}</span><input id="sideSearchInput" placeholder="搜索…"></label></div>
    <div class="cpq-nav-actions"><button id="homeNavCreatePanel" class="cpq-action active"><span class="cpq-action-icon">${homeNavIcon(2)}</span>新建清单</button><button class="cpq-action" data-home-view="mine"><span class="cpq-action-icon">${homeNavIcon(1)}</span>我的清单</button><button class="cpq-action" data-home-view="all"><span class="cpq-action-icon">${homeNavIcon(4)}</span>全部清单</button></div>
    <i class="cpq-panel-divider"></i><div class="cpq-section-title"><span class="cpq-section-icon">${homeNavIcon(3)}</span>历史对话</div><div class="cpq-nav-history" id="sideProjectList"><p class="home-nav-empty">正在加载…</p></div>
    <div class="cpq-nav-bottom-actions"><button class="cpq-action" id="homeNavSettingsPanel"><span class="cpq-action-icon">${homeNavIcon(6)}</span>模型与 API<i>›</i></button><a class="cpq-action" href="account.html"><span class="cpq-action-icon">${homeNavIcon(7)}</span>用户设置</a></div>
  </div>`;
  document.querySelector('#app').insertAdjacentHTML('beforeend', `<div class="home-llm-mask" id="homeLlmMask" hidden><section class="home-llm-dialog" role="dialog" aria-modal="true" aria-labelledby="homeLlmTitle"><header><h2 id="homeLlmTitle">模型设置</h2><button type="button" id="homeLlmClose" aria-label="关闭">×</button></header><div class="home-llm-body" id="homeLlmBody"></div><footer><button type="button" id="homeLlmCancel">关闭</button></footer></section></div>`);
}

function homeFileIcon(kind) {
  return kind === 'model'
    ? '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 3 4 7.5v9L12 21l8-4.5v-9L12 3Z"/><path d="m4 7.5 8 4.5 8-4.5M12 12v9"/></svg>'
    : '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8L14 2Z"/><path d="M14 2v6h6M8 13h8M8 17h6"/></svg>';
}
function homeFileSize(size) {
  if (!Number.isFinite(size)) return '';
  return size < 1024 * 1024 ? `${Math.max(1, Math.round(size / 1024))} KB` : `${(size / 1024 / 1024).toFixed(1)} MB`;
}
function syncHomeFiles(inputId, files) {
  const input = document.querySelector(`#${inputId}`);
  if (!input) return;
  const transfer = new DataTransfer();
  files.forEach(file => transfer.items.add(file));
  input.files = transfer.files;
}
function syncHomeDocumentInput() {
  syncHomeFiles('documentFiles', homeDocumentFiles);
}
function syncHomeModelInput() {
  syncHomeFiles('modelFile', homeModelFiles);
}
function renderHomeAttachments() {
  const container = document.querySelector('#uploadNames');
  if (!container) return;
  const entries = [
    ...homeModelFiles.map((file, index) => ({kind:'model', index, file, label:'模型图纸'})),
    ...homeDocumentFiles.map((file, index) => ({kind:'document', index, file, label:'技术文档'})),
  ];
  container.classList.toggle('has-files', entries.length > 0);
  container.innerHTML = entries.map(({kind, index, file, label}) => `<div class="home-file-chip" title="${homeEsc(file.name)}"><span class="home-file-icon ${kind}">${homeFileIcon(kind)}</span><span class="home-file-meta"><span class="home-file-name">${homeEsc(file.name)}</span><span class="home-file-type">${label}${file.size ? ` · ${homeFileSize(file.size)}` : ''}</span></span><button class="home-file-remove" type="button" data-kind="${kind}" data-index="${index}" aria-label="移除 ${homeEsc(file.name)}">×</button></div>`).join('');
  container.querySelectorAll('.home-file-remove').forEach(button => {
    button.onclick = () => {
      if (button.dataset.kind === 'model') {
        homeModelFiles.splice(Number(button.dataset.index), 1);
        syncHomeModelInput();
      } else {
        homeDocumentFiles.splice(Number(button.dataset.index), 1);
        syncHomeDocumentInput();
      }
      renderHomeAttachments();
    };
  });
}
function homeMountIndustrySelector() {
  const content = document.querySelector('.unified-content');
  if (!content || document.querySelector('#homeIndustry')) return;
  content.insertAdjacentHTML('beforeend', `<label class="home-industry-picker">行业模板<select id="homeIndustry" aria-label="选择行业模板"><option value="semiconductor">半导体</option><option value="battery">电池</option><option value="appliance">电器</option></select></label>`);
  const select = document.querySelector('#homeIndustry');
  select.value = homeIndustry;
  select.onchange = () => { homeIndustry = select.value; };
}
function bindHome() {
  const nav = document.querySelector('#homeNav'), navToggle = document.querySelector('#homeNavToggle');
  const navBrandToggle = document.querySelector('#homeNavBrandToggle');
  const collapsed = document.querySelector('#homeNavCollapsed'), expanded = document.querySelector('#homeNavExpanded');
  let navCloseTimer = 0;
  const toggleNav = open => {
    if (navCloseTimer) { window.clearTimeout(navCloseTimer); navCloseTimer = 0; }
    if (open) {
      nav.classList.add('is-open');
      nav.classList.remove('is-closing');
      collapsed.classList.add('hidden');
      expanded.classList.remove('closing');
      expanded.classList.add('open');
    } else {
      // 容器与展开面板同步收窄/左移；收起期间只保留 56px 窄容器，避免露出大块白底。
      nav.classList.add('is-closing');
      nav.classList.remove('is-open');
      // 折叠图标从点击收起的这一刻起就保持可见，整个收起过程不再二次切换。
      collapsed.classList.remove('hidden');
      expanded.classList.remove('open');
      expanded.classList.add('closing');
      navCloseTimer = window.setTimeout(() => {
        expanded.classList.remove('closing');
        nav.classList.remove('is-closing');
        navCloseTimer = 0;
      }, 280);
    }
    navToggle.setAttribute('aria-expanded', String(open));
    if (navBrandToggle) navBrandToggle.setAttribute('aria-expanded', String(open));
  };
  navToggle.onclick = () => toggleNav(!nav.classList.contains('is-open'));
  if (navBrandToggle) navBrandToggle.onclick = () => toggleNav(false);
  document.addEventListener('click', event => { if (!nav.contains(event.target) && nav.classList.contains('is-open')) toggleNav(false); });
  const switchHomeView = view => {
    activeTab=view; page=1;
    document.querySelectorAll('.list-tab').forEach(x=>x.classList.toggle('active', x.dataset.list===view));
    document.querySelectorAll('[data-home-view]').forEach(x=>x.classList.toggle('active', x.dataset.homeView===view));
    renderCards();
  };
  document.querySelectorAll('[data-home-view]').forEach(el=>el.onclick=()=>switchHomeView(el.dataset.homeView));
  const goCreate = () => { location.href='requirement-create.html'; };
  document.querySelector('#homeNavCreate').onclick=goCreate;
  document.querySelector('#homeNavCreatePanel').onclick=goCreate;
  document.querySelector('#homeNavHistory').onclick=()=>toggleNav(true);
  document.querySelector('#homeNavSearch').onclick=()=>{toggleNav(true);setTimeout(()=>document.querySelector('#sideSearchInput').focus(),0);};
  document.querySelector('#homeNavSettings').onclick=openHomeLlmSettings;
  document.querySelector('#homeNavSettingsPanel').onclick=openHomeLlmSettings;
  document.querySelectorAll('.category-tag').forEach(el=>el.onclick=()=>{document.querySelectorAll('.category-tag').forEach(x=>x.classList.remove('active'));el.classList.add('active');const cat=el.dataset.category;if(cat!=='ai')homeToast('该能力入口正在接入企业知识库，当前可使用 AI 工艺创建需求。');});
  document.querySelectorAll('.list-tab').forEach(el=>el.onclick=()=>switchHomeView(el.dataset.list));
  document.querySelector('#searchBtn').onclick=()=>{keyword=document.querySelector('#searchInput').value;page=1;renderCards();};
  document.querySelector('#searchInput').addEventListener('keydown',e=>{if(e.key==='Enter')document.querySelector('#searchBtn').click();});
  document.querySelector('#sideSearchInput').addEventListener('input', event=>{keyword=event.target.value;document.querySelector('#searchInput').value=keyword;page=1;renderCards();});
  const prompt=document.querySelector('#homePrompt'), counter=document.querySelector('#charCounter'); prompt.addEventListener('input',()=>{counter.textContent=`${prompt.value.length}/200`;counter.style.color=prompt.value.length>=200?'#ef4444':'';});
  homeMountIndustrySelector();
  const modelInput = document.querySelector('#modelFile');
  // 渲染模板兼容旧浏览器：运行时显式开启多选，避免遗漏静态模板中的同名 input。
  modelInput.multiple = true;
  modelInput.onchange = event => {
    const added = [...event.target.files].filter(file => !homeModelFiles.some(existing => existing.name === file.name && existing.size === file.size && existing.lastModified === file.lastModified));
    homeModelFiles.push(...added);
    syncHomeModelInput();
    renderHomeAttachments();
  };
  document.querySelector('#documentFiles').onchange = event => {
    const added = [...event.target.files].filter(file => !homeDocumentFiles.some(existing => existing.name === file.name && existing.size === file.size && existing.lastModified === file.lastModified));
    homeDocumentFiles.push(...added);
    syncHomeDocumentInput();
    renderHomeAttachments();
  };
  document.querySelector('#sendBtn').onclick=createFromHome;
  document.querySelector('#homeLlmClose').onclick=closeHomeLlmSettings;
  document.querySelector('#homeLlmCancel').onclick=closeHomeLlmSettings;
  document.querySelector('#homeLlmMask').onclick=event=>{if(event.target.id==='homeLlmMask')closeHomeLlmSettings();};
}
function renderHomeSidebarProjects() {
  const list=document.querySelector('#sideProjectList');
  if (!list) return;
  const rows=homeItems.slice(0,8);
  list.innerHTML=rows.length ? rows.map(item=>`<button type="button" class="home-nav-project" data-project-id="${homeEsc(item.project?.project_id)}" title="${homeEsc(item.requirement?.title || item.project?.project_name || item.project?.source_filename || '未命名项目')}"><span class="home-nav-project-marker" aria-hidden="true">${homeNavIcon(3)}</span><span class="home-nav-project-copy"><strong>${homeEsc(item.requirement?.title || item.project?.project_name || item.project?.source_filename || '未命名项目')}</strong></span></button>`).join('') : '<p class="home-nav-empty">暂无可查看项目</p>';
  list.querySelectorAll('.home-nav-project').forEach(button=>button.onclick=()=>{
    const item=homeItems.find(row=>row.project?.project_id===button.dataset.projectId);
    if (item) homeOpenProject(item);
  });
}

function closeHomeLlmSettings() { document.querySelector('#homeLlmMask').hidden=true; }
// 面板实现与 2.1 页 Agent 小窗共用 llm-settings-panel.js —— 两处各写一套表单，
// 正是之前「首页改的和智能体里显示的对不上」的根因。
function openHomeLlmSettings() {
  const mask=document.querySelector('#homeLlmMask'), body=document.querySelector('#homeLlmBody');
  mask.hidden=false;
  if(!window.LlmSettingsPanel){ body.textContent='模型设置面板未加载。'; return; }
  window.LlmSettingsPanel.mount(body, { onSaved:()=>homeToast('模型设置已保存，全局生效。') });
}

async function createFromHome() {
  const files=homeModelFiles, file=files[0], description=document.querySelector('#homePrompt').value.trim();
  if(!file){homeToast('请先上传至少一份模型图纸，再创建真实工艺需求。',true);return;}
  const button=document.querySelector('#sendBtn');button.disabled=true;
  button.setAttribute('aria-busy','true');
  try {
    const fd=new FormData();fd.append('file',file);files.slice(1).forEach(f=>fd.append('files',f));fd.append('note',description);homeDocumentFiles.forEach(f=>fd.append('attachments',f));
    const created=await api('/api/projects',{method:'POST',body:fd});
    const title=file.name.replace(/\.[^.]+$/,'')||'未命名工艺需求';
    const doc={project_id:created.project_id,requirement_no:'',title,status:'draft',data:{title,description,requirement_type:'工艺评估',industry:homeIndustry,industry_selection:homeIndustry}};
    try {
      await api(`/api/projects/${created.project_id}/requirement`,{method:'PUT',body:JSON.stringify(doc)});
    } catch (draftError) {
      // 图纸已安全创建时，不应把用户困在首页；1.1 可继续读取图纸并保存完整草稿。
      setProject(created.project_id);
      homeToast(`图纸已创建，但 1.1 草稿预填失败：${draftError.message}；已进入 1.1，可继续填写。`, true);
      location.href=`requirement-create.html?project=${encodeURIComponent(created.project_id)}`;
      return;
    }
    // 首页发送只保存上传内容和需求草稿；AI 文档解析由 1.1 的按钮显式触发。
    setProject(created.project_id);
    location.href=`requirement-create.html?project=${encodeURIComponent(created.project_id)}`;
  } catch(err){homeToast(err.message||'创建需求失败',true);button.disabled=false;button.removeAttribute('aria-busy');}
}
async function startHome() {
  // 界面先行渲染：即使清单接口暂时失败，也不能让首页成为空白页。
  renderHome();
  bindHome();
  renderCards();
  try {
    const me = await api('/api/me');
    homeUser = me?.user || {};
    const [requests, projects, techRecords] = await Promise.all([
      api('/api/requirements'),
      api('/api/projects'),
      api('/api/techprocess/records?biz=tech'),
    ]);
    homeItems = mergeHomeItems(requests?.items || [], Array.isArray(projects) ? projects : [], techRecords?.records || []);
    renderCards();
    renderHomeSidebarProjects();
  } catch (err) {
    if (/未登录|令牌|401/.test(err?.message || '')) {
      // 服务重启或认证密钥更新后，旧会话必然失效；明确回到登录页，而不是留下空白首页。
      location.replace('auth.html?next=home.html');
      return;
    }
    homeToast(`首页清单暂时加载失败：${err?.message || '未知错误'}`, true);
    renderCards();
  }
}
startHome();
