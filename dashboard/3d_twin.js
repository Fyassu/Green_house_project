// Khởi tạo biến toàn cục để sử dụng trong Three.js
let scene, camera, renderer, controls;
let greenhouseModel; 
let dirLight, hemiLight;
let rainSystem, rainGeo;
let nightLights = []; // Lưu danh sách các đèn hoặc vật liệu phát sáng ban đêm
let currentHour = 12;
let currentWeather = 'sunny';
let isPlayingTime = false;
let lastTimeMs = 0;

let scifiLightModel;
let scifiRectLight; // Biến cho đèn LED dài
let scifiLightMaterials = [];
let isScifiLightOn = false;

// Sức khỏe cây trên mô hình 3D — tô màu lá/quả theo evaluatePlant() (model.js),
// dùng chung cho cả dữ liệu thật (Wokwi) lẫn mô phỏng (simulation.js)
let plantHealthMaterials = [];
const PLANT_HEALTH_TINT = {
    GOOD:     { color: null,     mix: 0    },
    SLOW:     { color: 0xC9B458, mix: 0.18 }, // vàng nhạt — hơi thiếu sức sống
    DECLINE:  { color: 0x8B6B3D, mix: 0.45 }, // nâu héo
    CRITICAL: { color: 0x5A4632, mix: 0.70 }, // nâu sẫm, gần héo hẳn
    DEAD:     { color: 0x2B2620, mix: 0.90 }, // gần đen — cây chết
};

// Màu chữ hiển thị trên bảng "Danh sách Cảm biến (Live)" — trùng với bảng màu
// .plant-badge.X trong style.css (dùng cho badge chính ở tab Dashboard) để
// nhất quán giữa 2 nơi hiển thị cùng một trạng thái.
const PLANT_HEALTH_COLOR = {
    GOOD:     "var(--green)",
    SLOW:     "var(--yellow)",
    DECLINE:  "var(--orange)",
    CRITICAL: "var(--red)",
    DEAD:     "var(--purple)",
};

function applyPlantHealthTint(state) {
    // Không cache theo "state không đổi thì bỏ qua" — model cây tải bất đồng bộ
    // (gltfLoader), có thể vào plantHealthMaterials muộn hơn lần gọi đầu tiên;
    // gọi lại mỗi lần (chi phí không đáng kể, chỉ vài material) để chắc chắn
    // material mới nạp cũng được tô đúng màu ngay.
    const cfg = PLANT_HEALTH_TINT[state] || PLANT_HEALTH_TINT.GOOD;
    plantHealthMaterials.forEach(mat => {
        if (!mat.userData.baseColor) return;
        if (!cfg.color || cfg.mix <= 0) {
            mat.color.copy(mat.userData.baseColor);
        } else {
            mat.color.copy(mat.userData.baseColor).lerp(new THREE.Color(cfg.color), cfg.mix);
        }
    });
}

// Audio Variables
let audioListener;
let fanAudio;
let pumpAudio;
let rainAudio;
let alarmAudio;
let alarmStartTime = 0;
let alarmTriggeredThisDetection = false;

// Edit Mode Variables
let transformControls;
let editableObjects = [];
let isEditMode = false;
let raycaster = new THREE.Raycaster();
let mouse = new THREE.Vector2();

// Fan Variables
let isFanSpinning = false;
let fanBlades = [];
let fanSpeed = 0;          // Tốc độ hiện tại
const FAN_MAX_SPEED = 0.35; // Tốc độ tối đa
const FAN_ACCEL = 0.008;    // Gia tốc bật
const FAN_DECEL = 0.004;    // Giảm tốc khi tắt

// Sprinkler Variables
let spraySystem, spraySystem2, sprayGeo;
let isSprinklerOn = false;

// IoT LCD Variables
let iotCanvas, iotContext, iotTexture;
let isIoTBooted = false;
let securityModeEnabled = true; // Chức năng bảo vệ mặc định bật

// Sunshade Variables
let shadeNet;
let shadeContext, shadeTexture;
let targetStripeWidth = 0;   // Thay vì kéo giãn, ta đổi độ rộng của khe sáng (0 -> 32)
let currentStripeWidth = 0;

function createShadeNet() {
    // Canvas texture for louvers (lam chắn nắng)
    const canvas = document.createElement('canvas');
    canvas.width = 256;
    canvas.height = 256;
    shadeContext = canvas.getContext('2d');
    
    // Transparent background
    shadeContext.clearRect(0, 0, 256, 256);
    
    shadeTexture = new THREE.CanvasTexture(canvas);
    shadeTexture.wrapS = THREE.RepeatWrapping;
    shadeTexture.wrapT = THREE.RepeatWrapping;
    shadeTexture.repeat.set(6, 1); 
    shadeTexture.minFilter = THREE.NearestFilter;
    shadeTexture.magFilter = THREE.NearestFilter;
    shadeTexture.generateMipmaps = false;
    
    // Tạo lưới cắt nắng hình vòm (Half Cylinder) ôm sát mái nhà kính
    // Bán kính 150, chiều dài 750, nửa vòng tròn, bắt đầu từ -90 độ để vòm úp xuống đúng tâm
    const geo = new THREE.CylinderGeometry(150, 150, 600, 32, 1, true, -Math.PI / 2, Math.PI); 
    geo.rotateX(-Math.PI / 2); // Xoay nằm dọc theo trục Z, vòm cong hướng lên +Y
    
    const mat = new THREE.MeshLambertMaterial({
        map: shadeTexture,
        color: 0x444444,
        side: THREE.DoubleSide,
        transparent: true,
        alphaTest: 0.5 // Dùng 0.5 kết hợp NearestFilter để viền bóng đổ cực nét, không bị nhòe
    });
    
    shadeNet = new THREE.Mesh(geo, mat);
    // Vị trí tâm vòm (trục vòm nằm ở độ cao Y=55, bán kính 150 -> đỉnh vòm là 205 bao qua trần)
    shadeNet.position.set(0, 55, 0); 
    shadeNet.castShadow = true;
    shadeNet.visible = false;
    
    scene.add(shadeNet);
}

function drawIoTScreen(showData, t, s, h, l) {
    if (!iotContext) return;
    // Nền xanh lá cây của LCD
    iotContext.fillStyle = '#8cc63f';
    iotContext.fillRect(0, 0, 256, 64);
    
    if (showData) {
        iotContext.fillStyle = '#111';
        iotContext.font = 'bold 24px "Courier New", monospace';
        
        let tVal = (t !== undefined && t !== null) ? Number(t).toFixed(1) : "0.0";
        let sVal = (s !== undefined && s !== null) ? s : "0";
        iotContext.fillText(`T:${tVal} S:${sVal}%`, 10, 26);
        
        let hVal = (h !== undefined && h !== null) ? Number(h).toFixed(1) : "0.0";
        let lVal = (l !== undefined && l !== null) ? l : "0";
        iotContext.fillText(`H:${hVal}% L:${lVal}%`, 10, 54);
        
        // Vẽ lưới mô phỏng pixel LCD
        iotContext.strokeStyle = 'rgba(0,0,0,0.05)';
        iotContext.lineWidth = 1;
        for(let i=0; i<256; i+=4) {
            iotContext.beginPath(); iotContext.moveTo(i, 0); iotContext.lineTo(i, 64); iotContext.stroke();
        }
        for(let j=0; j<64; j+=4) {
            iotContext.beginPath(); iotContext.moveTo(0, j); iotContext.lineTo(256, j); iotContext.stroke();
        }
    }
    if (iotTexture) iotTexture.needsUpdate = true;
}

function setupIoTScreen(model) {
    iotCanvas = document.createElement('canvas');
    iotCanvas.width = 256;
    iotCanvas.height = 64;
    iotContext = iotCanvas.getContext('2d');
    
    iotTexture = new THREE.CanvasTexture(iotCanvas);
    iotTexture.magFilter = THREE.NearestFilter; 
    
    // Màn hình ảo (PlaneGeometry) dựa trên ước tính kích thước tủ
    const screenGeo = new THREE.PlaneGeometry(0.8, 0.2); 
    const screenMat = new THREE.MeshBasicMaterial({ map: iotTexture, side: THREE.DoubleSide });
    const screenMesh = new THREE.Mesh(screenGeo, screenMat);
    
    // Đặt tạm vào mặt hông (+X). Nếu bị sai mặt thì có thể xoay lại sau.
    screenMesh.position.set(0.24, 0.1, 0); 
    screenMesh.rotation.y = Math.PI / 2;
    model.add(screenMesh);
    
    drawIoTScreen(false); // Bắt đầu ở trạng thái trống (đang boot)
    
    setTimeout(() => {
        isIoTBooted = true;
        drawIoTScreen(true); // Hiển thị dữ liệu sau 2s
    }, 2000);
}

window.init3DTwin = function() {
    const container = document.getElementById('three-container');
    if (!container) return;

    // 1. SCENE
    scene = new THREE.Scene();
    // Bật sương mù (Fog) để che viền đường chân trời, tạo cảm giác không gian vô tận
    scene.fog = new THREE.FogExp2(0x87CEEB, 0.0005);
    scene.background = new THREE.Color(0x87CEEB); // Màu trời mặc định

    // 2. CAMERA (Góc chéo từ bên ngoài vào)
    camera = new THREE.PerspectiveCamera(60, container.clientWidth / container.clientHeight, 0.1, 5000);
    // Zoom out xa hơn một chút theo yêu cầu
    camera.position.set(450, 300, -400);

    // BẬT ĐÔI TAI CHO CAMERA ĐỂ NGHE ÂM THANH 3D
    audioListener = new THREE.AudioListener();
    camera.add(audioListener)

    // 3. RENDERER
    renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
    renderer.setSize(container.clientWidth, container.clientHeight);
    renderer.shadowMap.enabled = true;
    renderer.shadowMap.type = THREE.PCFSoftShadowMap;
    // Bật sRGB encoding để màu sắc trung thực hơn
    renderer.outputEncoding = THREE.sRGBEncoding;
    container.appendChild(renderer.domElement);

    // 4. CONTROLS (Xoay, zoom bằng chuột)
    controls = new THREE.OrbitControls(camera, renderer.domElement);
    controls.enableDamping = true;
    controls.dampingFactor = 0.25; 
    controls.rotateSpeed = 0.6;  // Giảm tốc độ xoay (mặc định 1.0)
    controls.panSpeed = 0.6;     // Giảm tốc độ kéo/trượt (mặc định 1.0)
    controls.zoomSpeed = 0.6;    // Giảm tốc độ cuộn chuột zoom (mặc định 1.0)
    controls.maxPolarAngle = Math.PI / 2 - 0.05; // Khóa không cho camera lọt xuống dưới mặt đất
    controls.minDistance = 100; // Không cho zoom quá sát
    controls.maxDistance = 800; // Giới hạn zoom xa để không thấy rìa của thế giới

    // In tọa độ Camera ra Console mỗi khi bạn xoay xong chuột để bạn dễ dàng copy
    controls.addEventListener('end', () => {
        console.log(`Góc Camera hiện tại -> Position: (${camera.position.x.toFixed(1)}, ${camera.position.y.toFixed(1)}, ${camera.position.z.toFixed(1)}) | Target: (${controls.target.x.toFixed(1)}, ${controls.target.y.toFixed(1)}, ${controls.target.z.toFixed(1)})`);
    });

    // 4.5. TRANSFORM CONTROLS (Chỉnh sửa vật thể)
    transformControls = new THREE.TransformControls(camera, renderer.domElement);
    
    let activeAxis = 'XZ'; // Mặc định là mặt đất (mặt phẳng XZ)
    
    transformControls.addEventListener('dragging-changed', function (event) {
        controls.enabled = !event.value; // Tắt quay camera khi đang kéo vật thể
    });

    // Bắt sự kiện click vào mũi tên/mặt phẳng để ghi nhớ hướng di chuyển
    transformControls.addEventListener('mouseDown', function () {
        if (transformControls.axis) {
            activeAxis = transformControls.axis; 
        }
    });

    scene.add(transformControls);

    // 4.6. DI CHUYỂN BẰNG BÀN PHÍM (SMART NUDGE - TỰ ĐỘNG NHẬN DIỆN MẶT ĐANG CHỌN)
    window.addEventListener('keydown', function(event) {
        // Chỉ cho phép dùng phím nếu đang ở Edit Mode và đã chọn 1 vật thể
        if (!isEditMode || !transformControls.object) return;

        // --- THÊM TÍNH NĂNG XOAY BẰNG PHÍM R ---
        if (event.key.toLowerCase() === 'r') {
            transformControls.object.rotation.y += Math.PI / 2; // Xoay ngang (Trái/Phải)
            return; 
        }
        if (event.key.toLowerCase() === 't') {
            transformControls.object.rotation.x += Math.PI / 2; // Xoay dọc (Ngửa/Úp)
            return; 
        }
        if (event.key.toLowerCase() === 'y') {
            transformControls.object.rotation.z += Math.PI / 2; // Xoay nghiêng
            return; 
        }

        // Chặn phím mũi tên làm cuộn trang web
        if (["ArrowUp", "ArrowDown", "ArrowLeft", "ArrowRight"].includes(event.key)) {
            event.preventDefault();
        } else {
            return; // Bỏ qua các phím khác
        }

        // Mặc định di chuyển 1 đơn vị
        let step = 1.0; 
        if (event.shiftKey) step = 10.0; // Giữ Shift: Chạy nhanh x10
        if (event.altKey) step = 0.1;    // Giữ Alt: Đi chậm để căn chỉnh milimet

        const obj = transformControls.object;
        
        // Ưu tiên trục đang đưa chuột vào, nếu không thì lấy mặt phẳng đã click trước đó
        const currentAxis = transformControls.axis || activeAxis;

        // Bẻ lái hướng đi của phím mũi tên dựa vào mặt đang chọn
        switch (currentAxis) {
            case 'X': // Bấm vào Mũi tên Đỏ
                if (event.key === 'ArrowLeft') obj.position.x -= step;
                if (event.key === 'ArrowRight') obj.position.x += step;
                break;
            case 'Y': // Bấm vào Mũi tên Xanh lá
                if (event.key === 'ArrowUp') obj.position.y += step;
                if (event.key === 'ArrowDown') obj.position.y -= step;
                break;
            case 'Z': // Bấm vào Mũi tên Xanh dương
                if (event.key === 'ArrowUp') obj.position.z -= step; // Tiến
                if (event.key === 'ArrowDown') obj.position.z += step; // Lùi
                break;
            case 'XY': // Bấm vào Hình vuông Đỏ-Xanh (Mặt đứng ngang)
                if (event.key === 'ArrowLeft') obj.position.x -= step;
                if (event.key === 'ArrowRight') obj.position.x += step;
                if (event.key === 'ArrowUp') obj.position.y += step;
                if (event.key === 'ArrowDown') obj.position.y -= step;
                break;
            case 'YZ': // Bấm vào Hình vuông Xanh-Xanh (Mặt đứng dọc)
                if (event.key === 'ArrowLeft') obj.position.z -= step;
                if (event.key === 'ArrowRight') obj.position.z += step;
                if (event.key === 'ArrowUp') obj.position.y += step;
                if (event.key === 'ArrowDown') obj.position.y -= step;
                break;
            case 'XZ': // Bấm vào Hình vuông Đỏ-Xanh dương (Mặt đất nền)
            default:
                if (event.key === 'ArrowLeft') obj.position.x -= step;
                if (event.key === 'ArrowRight') obj.position.x += step;
                if (event.key === 'ArrowUp') obj.position.z -= step;
                if (event.key === 'ArrowDown') obj.position.z += step;
                break;
        }
    }, { passive: false });

    // Chọn vật thể bằng Raycaster khi bật Edit Mode
    renderer.domElement.addEventListener('pointerdown', (event) => {
        if (!isEditMode) return;
        
        // Chuyển đổi tọa độ chuột (giới hạn trong vùng canvas)
        const rect = renderer.domElement.getBoundingClientRect();
        mouse.x = ((event.clientX - rect.left) / rect.width) * 2 - 1;
        mouse.y = -((event.clientY - rect.top) / rect.height) * 2 + 1;

        raycaster.setFromCamera(mouse, camera);

        // Kiểm tra va chạm với các vật thể có thể edit
        const intersects = raycaster.intersectObjects(editableObjects, true);

        if (intersects.length > 0) {
            // Tìm root Group của object (do glTF trả về các mesh lồng nhau)
            let selectedObject = intersects[0].object;
            while (selectedObject.parent && !editableObjects.includes(selectedObject)) {
                selectedObject = selectedObject.parent;
            }
            if (editableObjects.includes(selectedObject)) {
                
                // --- LOGIC KHÓA MÔ HÌNH ĐÃ EDIT ---
                // Kiểm tra xem mô hình này đã được khai báo tọa độ trong fixedPositions chưa
                const modelNameSet = new Set([
    "fan_1",
    "fan_2",
    "tomato_1",
    "tomato_2",
    "cantaloupe_1",
    "cantaloupe_2",
    "strawberry_1",
    "strawberry_2",
    "sprinkler_1",
    "sprinkler_2",
    "ldr_sensor",
    "iot_box",
    "pir_sensor"
]);

function isValidModelName(name) {
    return modelNameSet.has(name);
}
                if (isValidModelName(selectedObject.name)) {
                    console.log(`🔒 Mô hình [${selectedObject.name}] đã được chốt tọa độ. Không thể chỉnh sửa!`);
                    transformControls.detach(); // Hủy công cụ chọn
                } else {
                    // Nếu chưa có trong danh sách, cho phép chọn và chỉnh sửa
                    transformControls.attach(selectedObject);
                }
                
            }
        } else {
            // Click ra ngoài khoảng không -> bỏ chọn
            transformControls.detach();
        }
    });

    // 5. ÁNH SÁNG (LIGHTS)
    hemiLight = new THREE.HemisphereLight(0xffffff, 0x444444, 0.6);
    hemiLight.position.set(0, 500, 0);
    scene.add(hemiLight);

    dirLight = new THREE.DirectionalLight(0xffffff, 0.8);
    dirLight.position.set(200, 500, 300);
    dirLight.castShadow = true;
    dirLight.shadow.camera.top = 500;
    dirLight.shadow.camera.bottom = -500;
    dirLight.shadow.camera.left = -500;
    dirLight.shadow.camera.right = 500;
    dirLight.shadow.camera.near = 0.1;
    dirLight.shadow.camera.far = 2000;
    // Giảm 2048->1024 (implementation_plan.md mục 4.4): khác biệt thị giác
    // không đáng kể ở khoảng cách camera tổng quan nhà kính, giảm đáng kể chi
    // phí render 1 pass shadow map mỗi frame.
    dirLight.shadow.mapSize.width = 1024;
    dirLight.shadow.mapSize.height = 1024;
    dirLight.shadow.bias = -0.001;
    scene.add(dirLight);
    
    // 5.5 MƯA (RAIN SYSTEM)
    const rainCount = 5000;
    rainGeo = new THREE.BufferGeometry();
    const rainPositions = new Float32Array(rainCount * 3);
    for (let i = 0; i < rainCount; i++) {
        rainPositions[i * 3] = (Math.random() - 0.5) * 1000;     // x
        rainPositions[i * 3 + 1] = Math.random() * 500;          // y
        rainPositions[i * 3 + 2] = (Math.random() - 0.5) * 1000; // z
    }
    rainGeo.setAttribute('position', new THREE.BufferAttribute(rainPositions, 3));
    const rainMat = new THREE.PointsMaterial({
        color: 0xaaaaaa,
        size: 1.5,
        transparent: true,
        opacity: 0.6
    });
    rainSystem = new THREE.Points(rainGeo, rainMat);
    rainSystem.visible = false; // Mặc định ẩn
    scene.add(rainSystem);

    // 6.1 MÔI TRƯỜNG (GROUND) - SỬ DỤNG ẢNH TEXTURE BỀ MẶT ĐÁ
    const textureLoader = new THREE.TextureLoader();
    const groundTexture = textureLoader.load('models/rocky_terrain_02_diff_4k.webp', function() {
        console.log("Đã tải xong Texture bề mặt đá!");
    });
    // Lặp lại họa tiết để tránh bị mờ khi phóng to
    groundTexture.wrapS = THREE.RepeatWrapping;
    groundTexture.wrapT = THREE.RepeatWrapping;
    groundTexture.repeat.set(20, 20);

    const groundGeo = new THREE.PlaneGeometry(10000, 10000);
    const groundMat = new THREE.MeshStandardMaterial({ 
        map: groundTexture,
        roughness: 0.9, // Đá thì sần sùi
        metalness: 0.05
    });
    
    const ground = new THREE.Mesh(groundGeo, groundMat);
    ground.rotation.x = -Math.PI / 2;
    ground.position.y = -2; // Hạ mặt đất xuống một chút
    ground.receiveShadow = true;
    scene.add(ground);

    // 6.2 TẢI ĐÈN SCIFI VÀO NHÀ KÍNH (ACTUATOR)
    const gltfLoader = new THREE.GLTFLoader();
    // Bắt buộc để giải mã các model đã nén Draco ở Giai đoạn 1 (implementation_plan.md
    // mục 4.1) — thiếu bước này GLTFLoader không đọc được mesh, model không hiện ra.
    const dracoLoader = new THREE.DRACOLoader();
    dracoLoader.setDecoderPath('vendor/draco/');
    gltfLoader.setDRACOLoader(dracoLoader);
    gltfLoader.load('models/scifi_light_02/scene.gltf', function(gltf) {
        scifiLightModel = gltf.scene;
        
        // Căn chỉnh kích thước và treo đèn lên trần nhà kính
        // Tự động tính toán kích thước bao để scale cho chuẩn
        const box = new THREE.Box3().setFromObject(scifiLightModel);
        const size = box.getSize(new THREE.Vector3());
        
        // Đèn nên dài khoảng 40 đơn vị (nhỏ hơn 60 lúc trước)
        const scaleFactor = size.x > 0 ? (40 / size.x) : 0.15;
        scifiLightModel.scale.set(scaleFactor, scaleFactor, scaleFactor);  
        
        // Quay ngược đèn xuống dưới
        scifiLightModel.rotation.z = Math.PI; // Lật ngược 180 độ
        // (hoặc rotation.x = Math.PI tùy trục của mô hình)

        // Cập nhật lại Box sau khi scale & rotate để tìm tâm mới
        const scaledBox = new THREE.Box3().setFromObject(scifiLightModel);
        const center = scaledBox.getCenter(new THREE.Vector3());
        
        // Đưa tâm đèn về vị trí treo trên trần nhà kính (y = 90)
        scifiLightModel.position.x -= center.x;
        scifiLightModel.position.z -= center.z;
        scifiLightModel.position.y = 90 - (center.y - scifiLightModel.position.y);
        
        scifiLightModel.traverse(function(child) {
            if (child.isMesh) {
                child.castShadow = false; 
                child.receiveShadow = true;
                
                // Lấy material của bóng đèn
                if (child.material && (child.material.name.includes('38') || child.name.includes('38'))) {
                    scifiLightMaterials.push(child.material);
                    child.material.emissive = new THREE.Color(0x000000);
                    child.material.emissiveIntensity = 0;
                }
            }
        });
        
        // TẠO ĐÈN LED DÀI (RectAreaLight)
        // Cú pháp: (màu ánh sáng vàng nắng, cường độ ban đầu, chiều rộng, chiều dài)
        scifiRectLight = new THREE.RectAreaLight(0xffe0a3, 0, 40, 5); 
        
        // Đặt vị trí ngay dưới mặt kính của bóng đèn
        scifiRectLight.position.set(0, -5, 0); 
        // Hướng đèn chiếu thẳng xuống đất
        scifiRectLight.rotation.x = -Math.PI / 2; 
        
        // Gắn đèn vào mô hình để khi kéo mô hình đi đâu đèn đi theo đó
        scifiLightModel.add(scifiRectLight);
        
        scifiLightModel.position.set(0.2, 179.3, -2.4);
        scene.add(scifiLightModel);

        scifiPointLight = new THREE.SpotLight(0xffcc66, 0); // Đèn màu vàng ấm (0xffcc66)
        
        // Đặt vị trí phát sáng nằm ngay bên dưới mô hình đèn
        scifiPointLight.position.set(0.2, 175, -2.4); 
        // Điểm đích hướng thẳng tắp xuống sàn nhà
        scifiPointLight.target.position.set(0.2, 0, -2.4); 
        
        // Tinh chỉnh để ánh sáng tỏa rộng tối đa ra mọi hướng
        scifiPointLight.angle = Math.PI / 2.2; // Góc tỏa cực rộng (gần 90 độ)
        scifiPointLight.penumbra = 1.0;        // Viền ánh sáng mờ dần siêu tự nhiên (0 -> 1)
        scifiPointLight.decay = 1.5;           // Độ phai của ánh sáng theo khoảng cách
        scifiPointLight.distance = 600;        // Chiếu xa 600 đơn vị (phủ hết nhà kính)
        
        // Tắt đổ bóng cho đèn phụ (implementation_plan.md mục 4.4): đây là đèn
        // trang trí, chỉ giữ đổ bóng cho dirLight (nguồn sáng chính/mặt trời)
        // để tránh render thêm 1 shadow map pass không cần thiết mỗi frame.
        scifiPointLight.castShadow = false;
        scifiPointLight.shadow.bias = -0.001;
        
        scene.add(scifiPointLight);
        scene.add(scifiPointLight.target);
        
        console.log("Đã tải xong đèn Sci-Fi!");
        
        scifiLightModel.name = "scifi_light";
        editableObjects.push(scifiLightModel);
    });

    // 6.3 TẢI CÁC MÔ HÌNH CÂY CỐI & QUẠT (GLB)
    const fixedPositions = {
    "fan_1": { "x": 0.8, "y": 182.5, "z": -302.1 },
    "fan_2": { "x": 0.6, "y": 182.7, "z": 304.1 },
    "tomato_1": { "x": 90.1, "y": 44.3, "z": 201.0 },
    "tomato_2": { "x": -91.4, "y": 45.3, "z": 202.2 },
    "cantaloupe_1": { "x": 88.8, "y": 70.2, "z": 0.0 },
    "cantaloupe_2": { "x": -90.1, "y": 76.3, "z": 0.0 },
    "strawberry_1": { "x": -92.5, "y": 18.2, "z": -200.0 },
    "strawberry_2": { "x": 90.8, "y": 21.5, "z": -199.9 },

    "sprinkler_1": { "x": 0.1, "y": 181.4, "z": 132.6 },
    "sprinkler_2": { "x": -0.3, "y": 179.8, "z": -145.6 },

    "ldr_sensor": { "x": 81.9, "y": 128.5, "z": -286.4, },
    "iot_box": { "x": 94, "y": 107, "z": -288.5, "rotationY": 11 },
    "pir_sensor": { "x": 69.6, "y": 89.9, "z": -284.8, "rotationZ": 14.14 },

    // --- 6 CẢM BIẾN ĐẤT ĐƯỢC TÍNH TỪ TỌA ĐỘ CÂY (Y + 15) ---
    "soil_sensor_1": {"x": 127.6,"y": -3.5,"z": 204.9,"rotationY": 4.71},
    "soil_sensor_2": {"x": -124.2, "y": -3.3, "z": -11.2, "rotationY": 1.57,},
  "soil_sensor_3": {"x": 111, "y": -4.3, "z": -210.8, "rotationY": 4.71}, 

};

    const newModelPaths = [
        { name: 'tomato', url: 'models/tomato.glb', targetScale: 160 },
        { name: 'cantaloupe', url: 'models/cantaloupe.glb', targetScale: 163 },
        { name: 'strawberry', url: 'models/strawberry.glb', targetScale: 164 }
    ];

    newModelPaths.forEach(item => {
        gltfLoader.load(item.url, function(gltf) {
            let model1 = gltf.scene;
            
            // Auto scale
            const box = new THREE.Box3().setFromObject(model1);
            const size = box.getSize(new THREE.Vector3());
            const maxDim = Math.max(size.x, size.y, size.z);
            const scale = maxDim > 0 ? (item.targetScale / maxDim) : 1;
            model1.scale.set(scale, scale, scale);

            // Bật bóng + đăng ký material để tô màu theo sức khỏe cây
            model1.traverse(child => {
                if (child.isMesh) {
                    child.castShadow = true;
                    child.receiveShadow = true;
                    const mats = Array.isArray(child.material) ? child.material : [child.material];
                    mats.forEach(mat => {
                        if (mat && mat.color && !mat.userData.baseColor) {
                            mat.userData.baseColor = mat.color.clone();
                            plantHealthMaterials.push(mat);
                        }
                    });
                }
            });

            // Lấy tọa độ cố định
            let pos1 = fixedPositions[item.name + "_1"];
            if (pos1) model1.position.set(pos1.x, pos1.y, pos1.z);

            // Dâu tây: xoay ngang
            if (item.name === 'strawberry') {
                model1.rotation.y = Math.PI / 2;
            }

            model1.name = item.name + "_1";
            scene.add(model1);
            editableObjects.push(model1);

            // Nhân bản mô hình 2 (clone() dùng chung tham chiếu material với model1,
            // nên đã được đăng ký ở trên — không cần lặp lại traverse cho model2)
            let model2 = model1.clone();
            let pos2 = fixedPositions[item.name + "_2"];
            if (pos2) model2.position.set(pos2.x, pos2.y, pos2.z);
            model2.name = item.name + "_2";
            scene.add(model2);
            editableObjects.push(model2);
            
            console.log("Đã tải xong " + item.name + " x2");
        });
    });

    // 6.4 TẢI MÔ HÌNH QUẠT (OBJ + MTL)
    const audioLoader = new THREE.AudioLoader();
    fanAudio = new THREE.PositionalAudio(audioListener);
    
    audioLoader.load('assets/fan_noise.mp3', function(buffer) {
        fanAudio.setBuffer(buffer);
        fanAudio.setRefDistance(100); // Khoảng cách bắt đầu nhỏ dần
        fanAudio.setVolume(0); // Bắt đầu ở Volume 0
        fanAudio.setLoop(true);
        fanAudio.play(); // Play ngầm, tí nữa bật quạt mới kéo volume lên
    });

    // Khởi tạo âm thanh mưa (Ambient Audio - nghe đều khắp mọi nơi)
    rainAudio = new THREE.Audio(audioListener);
    audioLoader.load('assets/rain.mp3', function(buffer) {
        rainAudio.setBuffer(buffer);
        rainAudio.setLoop(true);
        rainAudio.setVolume(0);
        rainAudio.play();
    });

    // Khởi tạo âm thanh cảnh báo (Ambient Audio)
    alarmAudio = new THREE.Audio(audioListener);
    audioLoader.load('assets/warning.mp3', function(buffer) {
        alarmAudio.setBuffer(buffer);
        alarmAudio.setLoop(true);
        alarmAudio.setVolume(0);
    });

    const mtlLoader = new THREE.MTLLoader();
    mtlLoader.setPath('models/fan/');
    mtlLoader.load('fan.mtl', function(materials) {
        materials.preload();
        const objLoader = new THREE.OBJLoader();
        objLoader.setMaterials(materials);
        objLoader.setPath('models/fan/');
        objLoader.load('fan.obj', function(fanObj) {
            console.log('[Fan Debug] OBJ loaded! Root children:', fanObj.children.length);
            
            // Auto scale cho quạt
            const box = new THREE.Box3().setFromObject(fanObj);
            const size = box.getSize(new THREE.Vector3());
            const maxDim = Math.max(size.x, size.y, size.z);
            const scale = maxDim > 0 ? (25 / maxDim) : 1;
            fanObj.scale.set(scale, scale, scale);

            // OBJ mới gồm 2 phần: Blades và Frame
            let bladeMesh = null;
            fanObj.traverse(child => {
                if (child.isMesh && child.name.toLowerCase().includes('blade')) {
                    bladeMesh = child;
                }
            });
            
            // Nếu không tìm thấy theo tên, lấy tạm mesh đầu tiên
            if (!bladeMesh) bladeMesh = fanObj.children[0];
            if (bladeMesh) bladeMesh.name = '__blades__';
            
            console.log('[Fan Debug] bladeMesh found:', bladeMesh ? bladeMesh.name : 'none');

            fanObj.traverse(child => {
                if (child.isMesh) {
                    child.castShadow = true;
                    child.receiveShadow = true;
                }
            });

            // Tọa độ + xoay quạt 1
            let fp1 = fixedPositions["fan_1"];
            if (fp1) fanObj.position.set(fp1.x, fp1.y, fp1.z);
            fanObj.rotation.x = Math.PI;
            fanObj.name = "fan_1";
            scene.add(fanObj);
            editableObjects.push(fanObj);
            if (bladeMesh) fanBlades.push(bladeMesh);

            // Nhân bản quạt 2
            let fanObj2 = fanObj.clone();
            let fp2 = fixedPositions["fan_2"];
            if (fp2) fanObj2.position.set(fp2.x, fp2.y, fp2.z);
            fanObj2.rotation.x = Math.PI;
            fanObj2.name = "fan_2";
            scene.add(fanObj2);
            editableObjects.push(fanObj2);

            let bladeGroup2 = fanObj2.getObjectByName('__blades__');
            if (bladeGroup2) fanBlades.push(bladeGroup2);

            console.log('[Fan Debug] fanBlades total:', fanBlades.length);
        });
    });

    // 6.5 TẢI MÔ HÌNH VÒI PHUN VÀ TẠO HIỆU ỨNG PHUN SƯƠNG
    pumpAudio = new THREE.PositionalAudio(audioListener);
    audioLoader.load('assets/water_spray.mp3', function(buffer) {
        pumpAudio.setBuffer(buffer);
        pumpAudio.setRefDistance(100);
        pumpAudio.setVolume(0);
        pumpAudio.setLoop(true);
        pumpAudio.play();
    });

    gltfLoader.load('models/Micro Sprinkler.glb', function(gltf) {
        let sprinklerModel1 = gltf.scene;
        
        const box = new THREE.Box3().setFromObject(sprinklerModel1);
        const size = box.getSize(new THREE.Vector3());
        const maxDim = Math.max(size.x, size.y, size.z);
        
        // 1. SỬA: x2 kích thước mô hình (từ 15 lên 30)
        const scale = maxDim > 0 ? (30 / maxDim) : 1; 
        sprinklerModel1.scale.set(scale, scale, scale);

        sprinklerModel1.traverse(child => {
            if (child.isMesh) {
                child.castShadow = true;
                child.receiveShadow = true;
            }
        });

        // Đặt tọa độ vòi 1
        let sp1Pos = fixedPositions["sprinkler_1"];
        if (sp1Pos) {
            sprinklerModel1.position.set(sp1Pos.x, sp1Pos.y, sp1Pos.z);
        } else {
            sprinklerModel1.position.set(0, 150, 100); 
        }
        sprinklerModel1.name = "sprinkler_1";
        
        // --- TẠO HIỆU ỨNG HẠT PHUN SƯƠNG (MIST/FOG) ---
        const particleCount = 15000;
        sprayGeo = new THREE.BufferGeometry();
        const pPos = new Float32Array(particleCount * 3);
        const pVel = new Float32Array(particleCount * 3);
        
        for(let i=0; i<particleCount; i++) {
            pPos[i*3] = 0; pPos[i*3+1] = 0; pPos[i*3+2] = 0;
            
            let theta = Math.random() * Math.PI * 2;
            let phi = Math.random() * Math.PI / 4.5; 
            // Giảm tốc độ bay của hạt để thu hẹp bán kính phun (Local space scale = 30)
            let speed = 0.05 + Math.random() * 0.15;
            
            pVel[i*3] = Math.sin(phi) * Math.cos(theta) * speed;
            pVel[i*3+1] = -(Math.cos(phi) * speed); 
            pVel[i*3+2] = Math.sin(phi) * Math.sin(theta) * speed;
        }
        
        sprayGeo.setAttribute('position', new THREE.BufferAttribute(pPos, 3));
        sprayGeo.setAttribute('velocity', new THREE.BufferAttribute(pVel, 3));
        
        const sprayMat = new THREE.PointsMaterial({
            color: 0xffffff, 
            size: 0.35,      
            transparent: true,
            opacity: 0.5,   
            blending: THREE.AdditiveBlending, 
            depthWrite: false
        });
        
        spraySystem = new THREE.Points(sprayGeo, sprayMat);
        spraySystem.visible = false; 
        
        // 2. SỬA: Đưa điểm phun sát lại gần đầu vòi hơn (về 0 do vòi đã bị scale x30 lần)
        spraySystem.position.y = -0.3; 
        
        sprinklerModel1.add(spraySystem);
        scene.add(sprinklerModel1);
        editableObjects.push(sprinklerModel1); 
        
        // --- 3. SỬA: NHÂN BẢN THÊM VÒI SỐ 2 ---
        let sprinklerModel2 = sprinklerModel1.clone();
        
        // Đặt tọa độ vòi 2
        let sp2Pos = fixedPositions["sprinkler_2"];
        if (sp2Pos) {
            sprinklerModel2.position.set(sp2Pos.x, sp2Pos.y, sp2Pos.z);
        } else {
            sprinklerModel2.position.set(0, 150, -100);
        }
        sprinklerModel2.name = "sprinkler_2";

        // Trích xuất hệ thống sương của vòi 2 để dùng cho nút Bật/Tắt
        spraySystem2 = sprinklerModel2.children.find(child => child.isPoints);

        scene.add(sprinklerModel2);
        editableObjects.push(sprinklerModel2);

        console.log("Đã tải xong 2 Vòi phun sương treo trần!");
    });

    // 6.6 TẢI CÁC THIẾT BỊ IOT (TỦ ĐIỀU KHIỂN, CẢM BIẾN LDR, PIR)
    const iotModelPaths = [
        { name: 'ldr_sensor', url: 'models/ldr_sensor.glb', scaleMultiplier: 1000 },
        { name: 'iot_box', url: 'models/IoT_box.glb', targetScale: 60 },
        { name: 'pir_sensor', url: 'models/pir_sensor.glb', targetScale: 15 }
    ];

    iotModelPaths.forEach(item => {
        gltfLoader.load(item.url, function(gltf) {
            let model = gltf.scene;
            
            // Xử lý Scale (Tỷ lệ)
            if (item.scaleMultiplier) {
                // Ép kích thước tuyệt đối (Dành riêng cho LDR bị lỗi box)
                model.scale.set(item.scaleMultiplier, item.scaleMultiplier, item.scaleMultiplier);
            } else {
                // Tính toán tự động cho các mô hình bình thường
                const box = new THREE.Box3().setFromObject(model);
                const size = box.getSize(new THREE.Vector3());
                const maxDim = Math.max(size.x, size.y, size.z);
                const scale = maxDim > 0 ? (item.targetScale / maxDim) : 1;
                model.scale.set(scale, scale, scale);
            }

            model.traverse(child => {
                if (child.isMesh) {
                    child.castShadow = true;
                    child.receiveShadow = true;
                }
            });

            // Gán tọa độ & 3 góc xoay X, Y, Z
            let pos = fixedPositions[item.name];
            if (pos) {
                model.position.set(pos.x, pos.y, pos.z);
                if (pos.rotationX !== undefined) model.rotation.x = pos.rotationX;
                if (pos.rotationY !== undefined) model.rotation.y = pos.rotationY;
                if (pos.rotationZ !== undefined) model.rotation.z = pos.rotationZ;
            } else {
                model.position.set(0, 100, 0); 
            }

            model.name = item.name;
            scene.add(model);
            editableObjects.push(model);
            console.log(`Đã tải xong thiết bị: ${item.name}`);
            
            // Nếu là iot_box thì gắn thêm màn hình Ảo
            if (item.name === 'iot_box') {
                setupIoTScreen(model);
            }
        });
    });

    // 6.7 TẢI CẢM BIẾN ĐẤT (SOIL SENSOR)
    gltfLoader.load('models/soil_sensor.glb', function(gltf) {
        let baseModel = gltf.scene;

        baseModel.traverse(child => {
            if (child.isMesh) {
                child.castShadow = true;
                child.receiveShadow = true;
            }
        });

        // THÊM HITBOX ẢO ĐỂ DỄ CLICK KHI EDIT
        // (Do mô hình gốc quá mảnh khảnh, Raycaster khó trúng)
        const hitBoxGeo = new THREE.BoxGeometry(0.8, 2.5, 0.8);
        const hitBoxMat = new THREE.MeshBasicMaterial({ 
            transparent: true, 
            opacity: 0,        // Hoàn toàn trong suốt
            depthWrite: false  // Không che khuất các vật khác
        });
        const hitBox = new THREE.Mesh(hitBoxGeo, hitBoxMat);
        hitBox.position.y = 1.0; // Nâng hitbox lên bao trọn chiều cao cảm biến
        baseModel.add(hitBox);

        // Chỉnh số này để phóng to/thu nhỏ nếu cần
        let soilScaleMultiplier = 50; 
        baseModel.scale.set(soilScaleMultiplier, soilScaleMultiplier, soilScaleMultiplier);

        // Vòng lặp nhân bản ra 3 cái (1 cho mỗi loại cây)
        for (let i = 1; i <= 3; i++) {
            let model = baseModel.clone();
            let name = `soil_sensor_${i}`;
            model.name = name;

            let pos = fixedPositions[name];
            if (pos) {
                model.position.set(pos.x, pos.y, pos.z);
                if (pos.rotationX !== undefined) model.rotation.x = pos.rotationX;
                if (pos.rotationY !== undefined) model.rotation.y = pos.rotationY;
                if (pos.rotationZ !== undefined) model.rotation.z = pos.rotationZ;
            } else {
                model.position.set(0, 50, 0); 
            }

            scene.add(model);
            editableObjects.push(model); // Vẫn cho phép click, xoay và kéo thả
        }
        console.log("Đã tải xong 3 cảm biến đất!");
    });

    // 7. TẢI MÔ HÌNH FBX
    // Sử dụng LoadingManager để bẻ lái (Redirect) các đường dẫn Texture bị sai trong file FBX
    const manager = new THREE.LoadingManager();
    manager.setURLModifier((url) => {
        // Chỉ chặn các file ảnh, KHÔNG bẻ lái file .fbx gốc
        if (url.includes('models/Green_house/source/') && !url.endsWith('.fbx')) {
            // Trả về một bức ảnh 1x1 trong suốt (Base64) để chặn báo lỗi 404
            // Điều này cũng giúp loại bỏ luôn các vân hoa văn rườm rà
            return 'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII=';
        }
        return url;
    });

    const loader = new THREE.FBXLoader(manager);
    const loadingUI = document.getElementById('three-loading');
    const progressText = document.getElementById('three-progress');

    loader.load(
        'models/Green_house/source/green_house_1_5.fbx',
        function (object) {
            // Khi load thành công
            greenhouseModel = object;
            
            // LOG TÊN VÀ VỊ TRÍ CỦA TẤT CẢ MESH ĐỂ TÌM Ô ĐẦT CÂY
            console.log('=== DANH SÁCH MESH TRONG NHÀ KÍNH ===');
            const meshInfos = [];
            object.traverse(function(child) {
                if (child.isMesh) {
                    const wp = new THREE.Vector3();
                    child.getWorldPosition(wp);
                    meshInfos.push({ name: child.name, x: wp.x.toFixed(0), y: wp.y.toFixed(0), z: wp.z.toFixed(0) });
                }
            });
            // Sắp xếp theo Y (thấp nhất trước = gần mặt đất nhất) để dễ tìm ô
            meshInfos.sort((a, b) => parseFloat(a.y) - parseFloat(b.y));
            meshInfos.forEach(m => console.log('[Mesh]', m.name, '| pos:', m.x, m.y, m.z));
            console.log('=== HẾT DANH SÁCH ===');

            // Xử lý vật liệu và đổ bóng cho từng bộ phận bên trong mô hình
            object.traverse(function (child) {
                if (child.isMesh) {
                    child.castShadow = true;
                    child.receiveShadow = true;
                    
                    // Ẩn mặt đất màu trắng (nguyên bản của file FBX) vì ta đã làm mặt đất vô tận xịn hơn
                    const name = child.name.toLowerCase();
                    if (name.includes('plane') || name.includes('ground') || name.includes('terrain')) {
                        child.visible = false;
                    }

                    // Xóa triệt để các hoa văn (vân gỗ, vải) để lấy lại màu khối nguyên bản, phẳng, sang trọng
                    if (child.material) {
                        const cleanMat = (mat) => {
                            mat.map = null;
                            mat.normalMap = null;
                            mat.roughnessMap = null;
                            mat.metalnessMap = null;
                            mat.bumpMap = null;
                            // Thêm chút bóng bẩy cho nhựa/kính
                            if (mat.opacity < 1.0 || mat.transparent) {
                                mat.transparent = true;
                                mat.opacity = 0.5; // Kính bán trong suốt
                                mat.depthWrite = false;
                            }
                            // Lưu lại các material của bóng đèn để bật sáng ban đêm
                            if (child.name.toLowerCase().includes('light') || child.name.toLowerCase().includes('lamp') || child.name.toLowerCase().includes('bulb')) {
                                nightLights.push(mat);
                            }
                            
                            mat.needsUpdate = true;
                        };
                        if (Array.isArray(child.material)) child.material.forEach(cleanMat);
                        else cleanMat(child.material);
                    }
                }
            });

            // Cân chỉnh kích thước (Scale) cho vừa vặn
            object.scale.set(1, 1, 1); 
            
            // Tự động tính toán để đặt mô hình ra chính giữa (Center Pivot)
            const box = new THREE.Box3().setFromObject(object);
            const center = box.getCenter(new THREE.Vector3());
            object.position.x += (object.position.x - center.x);
            object.position.z += (object.position.z - center.z);
            // Giữ nguyên trục Y để mô hình nằm trên mặt đất

            scene.add(object);

            // Ẩn UI Loading
            if (loadingUI) loadingUI.style.display = 'none';
        },
        function (xhr) {
            // Đang load (cập nhật UI %)
            if (progressText && xhr.total > 0) {
                const percent = Math.round((xhr.loaded / xhr.total) * 100);
                progressText.textContent = percent + '%';
            } else if (progressText) {
                // Nếu server không trả về total size
                progressText.textContent = Math.round(xhr.loaded / 1024) + ' KB';
            }
        },
        function (error) {
            // Báo lỗi
            console.error('Lỗi khi tải mô hình FBX:', error);
            if (progressText) progressText.textContent = 'Lỗi tải mô hình!';
        }
    );

    // Bắt sự kiện resize trình duyệt
    window.addEventListener('resize', onWindowResize, false);

    // Bắt đầu vòng lặp Render
    animate();
    initSimulationUI();
    console.log("3D Twin Initialized with FBXLoader!");

    // Tạo một vòng lặp kín bên trong 3d_twin.js để kiểm tra trạng thái tải
    const checkAllLoaded = setInterval(() => {
        // Kiểm tra xem đã có FBX (nhà kính) và mảng đủ 9 vật thể con (1 đèn, 2 quạt, 6 cây) chưa
        if (greenhouseModel && editableObjects.length >= 9) {
            window.is3DReady = true;    // Kích hoạt cờ!
            clearInterval(checkAllLoaded); // Dừng vòng lặp
        }
    }, 500);
};

// -------------------------------------------------------------
// LOGIC MÔ PHỎNG MÔI TRƯỜNG (THỜI GIAN, MẶT TRỜI, THỜI TIẾT)
// -------------------------------------------------------------

function initSimulationUI() {
    const slider = document.getElementById('sim-time-slider');
    const display = document.getElementById('sim-time-display');
    const weatherBtns = document.querySelectorAll('.btn-weather');

    if (slider) {
        slider.addEventListener('input', (e) => {
            currentHour = parseFloat(e.target.value);
            
            // Format time display (VD: 12.5 -> 12:30)
            const h = Math.floor(currentHour);
            const m = (currentHour - h) * 60;
            display.textContent = `${h.toString().padStart(2, '0')}:${m.toString().padStart(2, '0')}`;

            updateSimulation();
            // Kéo tay thanh thời gian cũng phản ánh ngay vào dữ liệu mô phỏng
            // (nếu đang bật) — chủ yếu để ánh sáng theo baseline giờ mới ngay
            // lập tức, xem simulation.js.
            if (typeof runSimTick === 'function') runSimTick(0);
        });
    }

    if (weatherBtns) {
        weatherBtns.forEach(btn => {
            if (!btn.getAttribute('data-weather')) return; // Bỏ qua nút không phải nút thời tiết
            btn.addEventListener('click', (e) => {
                // Xóa active cũ chỉ trong nhóm nút thời tiết
                weatherBtns.forEach(b => { if (b.getAttribute('data-weather')) b.classList.remove('active'); });
                // Thêm active mới
                const target = e.currentTarget;
                target.classList.add('active');
                currentWeather = target.getAttribute('data-weather');
                
                updateSimulation();
            });
        });
    }

    const playBtn = document.getElementById('sim-play-btn');
    if (playBtn) {
        playBtn.addEventListener('click', () => {
            isPlayingTime = !isPlayingTime;
            playBtn.innerHTML = isPlayingTime ? '<i class="fa-solid fa-pause"></i>' : '<i class="fa-solid fa-play"></i>';
            if (isPlayingTime) lastTimeMs = performance.now();
        });
    }

    // EXPOSE GLOBAL FUNCTION TO RECEIVE LIVE DATA FROM APP.JS
    window.update3DTwin = function(data) {
        if (!data) return;

        // 1. UPDATE SENSORS UI
        const elDht = document.getElementById('sim-live-dht');
        if (elDht) elDht.innerHTML = `${Number(data.temperature).toFixed(1)}&deg;C | ${Number(data.humidity).toFixed(1)}%`;
        
        const elLdr = document.getElementById('sim-live-ldr');
        if (elLdr) elLdr.innerHTML = `${data.light_level}%`;
        
        const elSoil = document.getElementById('sim-live-soil');
        if (elSoil) elSoil.innerHTML = `${data.soil_moisture}%`;
        
        const elPir = document.getElementById('sim-live-pir');
        if (elPir) elPir.innerHTML = data.motion_detected ? "Có người" : "Không";

        // Trạng thái sức khỏe cây — evaluatePlant() (model.js), dùng chung với
        // badge tab Dashboard và hiệu ứng tô màu cây 3D bên dưới (mục 4)
        const plantState = typeof evaluatePlant === 'function'
            ? evaluatePlant(data.temperature, data.soil_moisture, data.humidity)
            : null;
        const elPlant = document.getElementById('sim-live-plant');
        if (elPlant && plantState) {
            elPlant.textContent = plantState;
            elPlant.style.color = PLANT_HEALTH_COLOR[plantState] || "";
        }

        // 2. UPDATE ACTUATORS UI & 3D MODELS
        const now = Date.now();
        const pending = window.pendingCmds || { fan: 0, pump: 0, servo: 0, light: 0 };

        const elFan = document.getElementById('sim-live-fan');
        if (now - pending.fan > 25000) {
            if (elFan) {
                elFan.innerHTML = data.fan_status ? "ON" : "OFF";
                elFan.className = "actuator-badge " + (data.fan_status ? "on" : "off");
            }
            isFanSpinning = data.fan_status;
        }

        const elPump = document.getElementById('sim-live-pump');
        if (now - pending.pump > 25000) {
            if (elPump) {
                elPump.innerHTML = data.pump_status ? "ON" : "OFF";
                elPump.className = "actuator-badge " + (data.pump_status ? "on" : "off");
            }
            isSprinklerOn = data.pump_status;
            if (spraySystem) spraySystem.visible = isSprinklerOn;
            if (spraySystem2) spraySystem2.visible = isSprinklerOn;
            // Bật âm thanh nếu user đã tương tác trang web (resume context) và màn load đã xong
            if (pumpAudio && audioListener && audioListener.context.state === 'running') {
                pumpAudio.setVolume((window.is3DReady === true && isSprinklerOn) ? 4.0 : 0);
            }
        }

        const elRoof = document.getElementById('sim-live-roof');
        if (now - pending.servo > 25000) {
            if (elRoof) {
                elRoof.innerHTML = data.servo_angle + "&deg;";
                elRoof.className = "actuator-badge " + (data.servo_angle > 0 ? "on" : "off");
            }
            if (data.servo_angle === 0) targetStripeWidth = 0;
            else if (data.servo_angle === 45) targetStripeWidth = 32;
            else targetStripeWidth = 64;
        }

        const elLight = document.getElementById('sim-live-light');
        if (now - pending.light > 25000) {
            if (elLight) {
                elLight.innerHTML = data.light_status ? "ON" : "OFF";
                elLight.className = "actuator-badge " + (data.light_status ? "on" : "off");
            }
            if (typeof scifiRectLight !== 'undefined' && scifiRectLight) scifiRectLight.intensity = data.light_status ? 5.0 : 0;
            if (typeof scifiPointLight !== 'undefined' && scifiPointLight) scifiPointLight.intensity = data.light_status ? 10.0 : 0;
            if (typeof scifiLightMaterials !== 'undefined' && Array.isArray(scifiLightMaterials)) {
                scifiLightMaterials.forEach(mat => {
                    mat.emissive = new THREE.Color(data.light_status ? 0xffcc66 : 0x000000);
                    mat.emissiveIntensity = data.light_status ? 2.0 : 0;
                });
            }
        }

        // Bật còi cảnh báo khi phát hiện chuyển động và ĐANG BẬT chế độ bảo vệ (securityModeEnabled === true)
        if (data.motion_detected && securityModeEnabled) {
            if (!alarmTriggeredThisDetection) {
                alarmTriggeredThisDetection = true;
                alarmStartTime = Date.now();
                if (alarmAudio && !alarmAudio.isPlaying && window.is3DReady === true) {
                    alarmAudio.setVolume(2.0);
                    alarmAudio.play();
                }
            } else {
                // Nếu còi đã kêu quá 10 giây thì tự động ngắt còi
                if (Date.now() - alarmStartTime > 10000) {
                    if (alarmAudio && alarmAudio.isPlaying) {
                        alarmAudio.stop();
                    }
                }
            }
        } else {
            // Reset cờ kích hoạt còi khi hết chuyển động hoặc khi tắt bảo vệ
            alarmTriggeredThisDetection = false;
            if (alarmAudio && alarmAudio.isPlaying) {
                alarmAudio.stop();
            }
        }

        const elSecurity = document.getElementById('sim-live-security');
        if (elSecurity && now - pending.security > 25000) {
            if (data.security_mode && data.motion_detected) {
                elSecurity.innerHTML = '<i class="fa-solid fa-triangle-exclamation fa-beat"></i> BÁO ĐỘNG';
                elSecurity.className = "actuator-badge alert";
            } else {
                elSecurity.innerHTML = data.security_mode ? "BẬT" : "TẮT";
                elSecurity.className = "actuator-badge " + (data.security_mode ? "on" : "off");
            }
            securityModeEnabled = data.security_mode;
        }

        // 3. UPDATE 3D LCD SCREEN (Giống hệt LCD Wokwi: T, S và H, L)
        drawIoTScreen(true, data.temperature, data.soil_moisture, data.humidity, data.light_level);

        // 4. TÔ MÀU CÂY THEO SỨC KHỎE (dùng lại plantState đã tính ở mục 1)
        if (plantState) {
            applyPlantHealthTint(plantState);
        }
    };

    const btnEditMode = document.getElementById('btn-edit-mode');
    const btnExportPos = document.getElementById('btn-export-pos');
    if (btnEditMode && btnExportPos) {
        btnEditMode.addEventListener('click', () => {
            isEditMode = !isEditMode;
            if (isEditMode) {
                btnEditMode.classList.add('active');
                btnEditMode.innerHTML = '<i class="fa-solid fa-cube" style="color: yellow;"></i> Đang bật Edit Mode';
                btnExportPos.style.display = 'block';
            } else {
                btnEditMode.classList.remove('active');
                btnEditMode.innerHTML = '<i class="fa-solid fa-cube"></i> Bật Edit Mode';
                btnExportPos.style.display = 'none';
                transformControls.detach(); // Bỏ chọn khi tắt
            }
        });

        btnExportPos.addEventListener('click', () => {
            let result = {};
            editableObjects.forEach(obj => {
                let objData = {
                    x: parseFloat(obj.position.x.toFixed(1)),
                    y: parseFloat(obj.position.y.toFixed(1)),
                    z: parseFloat(obj.position.z.toFixed(1))
                };
                
                if (obj.name.includes('soil_sensor')) {
                    objData.rotationX = parseFloat(obj.rotation.x.toFixed(2));
                    objData.rotationY = parseFloat(obj.rotation.y.toFixed(2));
                    objData.rotationZ = parseFloat(obj.rotation.z.toFixed(2));
                }
                
                result[obj.name] = objData;
            });
            console.log("TỌA ĐỘ MÔ HÌNH HIỆN TẠI:\n" + JSON.stringify(result, null, 2));
            alert("Đã in tọa độ và góc xoay ra Console (nhấn F12 để copy)!");
        });
    }

    // Chế độ demo nhẹ (Giai đoạn 4 - implementation_plan.md mục 4.4): tắt đổ
    // bóng + giảm độ phân giải render + tắt âm thanh để chạy mượt trên máy yếu
    // khi demo. Không đụng vào rainSystem.visible/currentWeather (do updateSceneLighting
    // đã tự quản lý theo thời tiết) để tránh xung đột trạng thái.
    const btnDemoLite = document.getElementById('btn-demo-lite');
    if (btnDemoLite) {
        btnDemoLite.addEventListener('click', () => {
            window.setDemoLiteMode(!window.isDemoLiteMode);
            if (window.isDemoLiteMode) {
                btnDemoLite.classList.add('active');
                btnDemoLite.innerHTML = '<i class="fa-solid fa-bolt" style="color: yellow;"></i> Tắt Chế độ demo nhẹ';
            } else {
                btnDemoLite.classList.remove('active');
                btnDemoLite.innerHTML = '<i class="fa-solid fa-bolt"></i> Bật Chế độ demo nhẹ';
            }
        });
    }

    const lightBtn = document.getElementById('sim-light-btn');
    if (lightBtn) {
        lightBtn.addEventListener('click', () => {
            isScifiLightOn = !isScifiLightOn;
            
            if (isScifiLightOn) {
                lightBtn.classList.add('active');
                lightBtn.innerHTML = '<i class="fa-solid fa-lightbulb" style="color: yellow;"></i> Tắt Đèn';
                
                // Bật sáng đèn SpotLight chiếu xuống nền (Cường độ 10.0 là rất rực rỡ)
                if (scifiPointLight) scifiPointLight.intensity = 10.0; 
                
                // Bật sáng phần vỏ của mô hình bóng đèn
                scifiLightMaterials.forEach(mat => {
                    mat.emissive = new THREE.Color(0xffcc66);
                    mat.emissiveIntensity = 2.0; 
                });
            } else {
                lightBtn.classList.remove('active');
                lightBtn.innerHTML = '<i class="fa-solid fa-lightbulb"></i> Bật Đèn';
                
                // Tắt hoàn toàn
                if (scifiPointLight) scifiPointLight.intensity = 0;
                scifiLightMaterials.forEach(mat => {
                    mat.emissive = new THREE.Color(0x000000);
                    mat.emissiveIntensity = 0;
                });
            }
        });
    }

    const fanBtn = document.getElementById('sim-fan-btn');
    if (fanBtn) {
        fanBtn.addEventListener('click', () => {
            // SỬA: Đánh thức AudioContext của trình duyệt (Bắt buộc để lách luật chặn âm thanh tự động)
            if (audioListener && audioListener.context.state === 'suspended') {
                audioListener.context.resume();
            }

            isFanSpinning = !isFanSpinning;
            if (isFanSpinning) {
                fanBtn.classList.add('active');
                fanBtn.innerHTML = '<i class="fa-solid fa-fan" style="color: cyan;"></i> Tắt Quạt';
            } else {
                fanBtn.classList.remove('active');
                fanBtn.innerHTML = '<i class="fa-solid fa-fan"></i> Bật Quạt';
            }
        });
    }

    const pumpBtn = document.getElementById('sim-pump-btn');
    if (pumpBtn) {
        pumpBtn.addEventListener('click', () => {
            if (audioListener && audioListener.context.state === 'suspended') {
                audioListener.context.resume();
            }

            isSprinklerOn = !isSprinklerOn;
            
            if (isSprinklerOn) {
                pumpBtn.classList.add('active');
                pumpBtn.innerHTML = '<i class="fa-solid fa-droplet" style="color: #4da6ff;"></i> Tắt Bơm';
                
                if (spraySystem) spraySystem.visible = true;
                if (spraySystem2) spraySystem2.visible = true; 
                if (pumpAudio) pumpAudio.setVolume(4.0);
            } else {
                pumpBtn.classList.remove('active');
                pumpBtn.innerHTML = '<i class="fa-solid fa-droplet"></i> Bật Bơm';
                if (pumpAudio) pumpAudio.setVolume(0);
            }
        });
    }

    // --- SUNSHADE (MÁI CHE MÁI KÍNH TỰ ĐỘNG) UI ---
    createShadeNet();
    const shadeBtns = [
        { id: 'btn-shade-0', targetWidth: 0 },    // Mở 0 độ
        { id: 'btn-shade-45', targetWidth: 32 },  // 45 độ (nửa kín nửa hở)
        { id: 'btn-shade-90', targetWidth: 64 }   // 90 độ (đóng kín hoàn toàn)
    ];
    
    shadeBtns.forEach(item => {
        const btn = document.getElementById(item.id);
        if (btn) {
            btn.addEventListener('click', (e) => {
                shadeBtns.forEach(b => document.getElementById(b.id)?.classList.remove('active'));
                e.target.classList.add('active');
                targetStripeWidth = item.targetWidth;
            });
        }
    });

    // Gắn sự kiện cho các Input của IoT Screen
    const inTemp = document.getElementById('iot-input-temp');
    const inSoil = document.getElementById('iot-input-soil');
    const inStat = document.getElementById('iot-input-status');
    if (inTemp) inTemp.addEventListener('input', () => drawIoTScreen(isIoTBooted));
    if (inSoil) inSoil.addEventListener('input', () => drawIoTScreen(isIoTBooted));
    if (inStat) inStat.addEventListener('change', () => drawIoTScreen(isIoTBooted));

    // Chạy lần đầu để setup đúng trạng thái
    updateSimulation();
}

function updateSimulationTimeUI() {
    const slider = document.getElementById('sim-time-slider');
    const display = document.getElementById('sim-time-display');
    if (slider) slider.value = currentHour;
    
    if (display) {
        const h = Math.floor(currentHour);
        const m = Math.floor((currentHour - h) * 60);
        display.textContent = `${h.toString().padStart(2, '0')}:${m.toString().padStart(2, '0')}`;
    }
}

function updateSimulation() {
    if (!dirLight || !hemiLight || !scene) return;

    // 1. TÍNH TOÁN VỊ TRÍ MẶT TRỜI DỰA TRÊN THỜI GIAN
    // 0h: -90 độ, 6h: 0 độ, 12h: 90 độ, 18h: 180 độ
    const theta = (currentHour / 24) * Math.PI * 2 - Math.PI / 2;
    const radius = 500;
    
    // Mặt trời mọc ở trục X, cao độ ở trục Y
    dirLight.position.x = Math.cos(theta) * radius;
    dirLight.position.y = Math.sin(theta) * radius;
    dirLight.position.z = 200; // Lệch 1 chút về phía Z để có bóng đổ chéo

    // Ban đêm (Mặt trời lặn xuống dưới đất)
    const altitude = Math.sin(theta);
    const isNight = altitude <= 0;

    let targetColor = new THREE.Color();
    let targetIntensity = 0;
    let targetHemiIntensity = 0;

    // Tính toán màu sắc mượt mà (Lerp) theo độ cao mặt trời
    if (altitude < -0.1) {
        // Đêm đen
        targetColor.setHex(0x050510);
        targetIntensity = 0;
        targetHemiIntensity = 0.02; // Tối gần như hoàn toàn
    } else if (altitude >= -0.1 && altitude < 0.2) {
        // Chuyển tiếp Đêm <-> Bình minh/Hoàng hôn
        const t = (altitude + 0.1) / 0.3;
        targetColor.setHex(0x050510).lerp(new THREE.Color(0xffaa55), t);
        targetIntensity = t * 0.5;
        targetHemiIntensity = 0.02 + t * 0.4;
    } else if (altitude >= 0.2 && altitude < 0.5) {
        // Chuyển tiếp Bình minh <-> Trưa nắng
        const t = (altitude - 0.2) / 0.3;
        targetColor.setHex(0xffaa55).lerp(new THREE.Color(0x87ceeb), t);
        targetIntensity = 0.5 + t * 0.5;
        targetHemiIntensity = 0.42 + t * 0.28;
    } else {
        // Trưa nắng
        targetColor.setHex(0x87ceeb);
        targetIntensity = 1.0;
        targetHemiIntensity = 0.7;
    }

    dirLight.color = new THREE.Color(0xffffff);

    // Tắt hoàn toàn đèn nhà kính theo yêu cầu
    nightLights.forEach(mat => {
        mat.emissive = new THREE.Color(0x000000);
        mat.emissiveIntensity = 0;
    });

    // 3. THAY ĐỔI THEO THỜI TIẾT (Trời mưa/Nhiều mây)
    rainSystem.visible = false;
    if (rainAudio && rainAudio.hasPlaybackControl) rainAudio.setVolume(0); // Mặc định tắt tiếng mưa
    
    if (currentWeather === 'cloudy' && !isNight) {
        targetColor.lerp(new THREE.Color(0x708090), 0.8);
        targetIntensity *= 0.2; // Tối lại
        targetHemiIntensity = 0.4;
        dirLight.castShadow = false;
    } else if (currentWeather === 'rainy') {
        if (!isNight) targetColor.lerp(new THREE.Color(0x4a5a6a), 0.9);
        targetIntensity *= 0.1;
        dirLight.castShadow = false;
        rainSystem.visible = true; // Hiện mưa
        if (rainAudio && rainAudio.hasPlaybackControl) {
            rainAudio.setVolume(window.is3DReady === true ? 1.0 : 0);
        } // Bật tiếng mưa to khi sẵn sàng
    } else {
        // Nắng (Mặc định)
        if (!isNight) dirLight.castShadow = true;
    }

    // Áp dụng màu sắc và cường độ
    dirLight.intensity = targetIntensity;
    hemiLight.intensity = targetHemiIntensity;
    scene.background = targetColor;
    scene.fog.color = targetColor;
}

function onWindowResize() {
    const container = document.getElementById('three-container');
    if (!container) return;
    camera.aspect = container.clientWidth / container.clientHeight;
    camera.updateProjectionMatrix();
    renderer.setSize(container.clientWidth, container.clientHeight);
}

window._3dAnimFrameId = null;

window.start3DAnimation = function() {
    if (typeof audioListener !== 'undefined' && audioListener && audioListener.context.state === 'suspended') {
        audioListener.context.resume();
    }
    if (!window._3dAnimFrameId && window.is3DTabActive) {
        animate();
    }
};

window.stop3DAnimation = function() {
    if (typeof audioListener !== 'undefined' && audioListener && audioListener.context.state === 'running') {
        audioListener.context.suspend();
    }
};

// Chế độ demo nhẹ (Giai đoạn 4 - implementation_plan.md mục 4.4): tắt đổ bóng
// (renderer.shadowMap.enabled là công tắc tổng, không phụ thuộc castShadow của
// từng đèn) + giảm devicePixelRatio + tắt âm thanh qua master volume của
// AudioListener (không cần sửa từng chỗ set volume riêng lẻ theo actuator).
window.isDemoLiteMode = false;
window.setDemoLiteMode = function(enabled) {
    window.isDemoLiteMode = enabled;
    if (renderer) {
        renderer.shadowMap.enabled = !enabled;
        // Mặc định renderer chưa từng gọi setPixelRatio nên đang ở baseline =1
        // (Three.js default) — giữ nguyên =1 khi tắt lite mode, chỉ HẠ xuống
        // 0.75 khi bật lite mode để giảm tải thực sự cho máy yếu (không đặt
        // cao hơn 1 cho chế độ thường vì sẽ làm NẶNG hơn hiện trạng trên màn
        // hình DPI cao, ngược với mục tiêu "mượt hơn").
        renderer.setPixelRatio(enabled ? 0.75 : 1);
    }
    if (audioListener && typeof audioListener.setMasterVolume === 'function') {
        audioListener.setMasterVolume(enabled ? 0 : 1);
    }
};

function animate() {
    if (!window.is3DTabActive) {
        window._3dAnimFrameId = null;
        return; // Dừng vòng lặp render khi ở tab khác để tiết kiệm CPU/GPU
    }
    
    window._3dAnimFrameId = requestAnimationFrame(animate);
    
    // Animate Time
    if (isPlayingTime) {
        const now = performance.now();
        const delta = (now - lastTimeMs) / 1000.0;
        lastTimeMs = now;
        
        // 1 giây đời thực = 1 giờ mô phỏng (Có thể chỉnh chậm lại bằng cách nhân nhỏ hơn)
        // Giảm còn 1/2 tốc độ gốc (1.5 -> 0.75) theo yêu cầu — đồng bộ với SIM_RATE
        // (simulation.js) cũng giảm 1/2 để 2 tốc độ giữ nguyên tỉ lệ ban đầu.
        currentHour += delta * 0.75;
        if (currentHour >= 24) currentHour = 0; // Quay về 0h hôm sau
        
        updateSimulationTimeUI();
        updateSimulation();
        // Chế độ mô phỏng dữ liệu (simulation.js) — cùng đồng hồ currentHour ở
        // trên, để cảnh 3D (mặt trời/bầu trời) và dữ liệu cảm biến giả lập
        // luôn khớp nhau, không lệch pha.
        if (typeof runSimTick === 'function') runSimTick(delta);
    }

    // Animate Rain
    if (rainSystem && rainSystem.visible) {
        const positions = rainGeo.attributes.position.array;
        for (let i = 0; i < positions.length; i += 3) {
            positions[i + 1] -= 5; // Tốc độ rơi
            if (positions[i + 1] < 0) {
                positions[i + 1] = 500; // Reset lên trời
            }
        }
        rainGeo.attributes.position.needsUpdate = true;
    }
    
    // Animate Shade Net (Louver rotation simulation)
    if (shadeNet && shadeContext && shadeTexture) {
        if (Math.abs(targetStripeWidth - currentStripeWidth) > 0.1) {
            currentStripeWidth += (targetStripeWidth - currentStripeWidth) * 0.1;
            
            if (currentStripeWidth < 0.5) {
                shadeNet.visible = false;
            } else {
                shadeNet.visible = true;
                shadeContext.clearRect(0, 0, 256, 256);
                shadeContext.fillStyle = '#111111'; // Màu lam chắn
                
                if (currentStripeWidth >= 63.5) {
                    // Đóng kín 90 độ
                    shadeContext.fillRect(0, 0, 256, 256);
                } else {
                    // Hé 45 độ -> Các thanh lam in bóng đan xen
                    for (let i = 0; i < 256; i += 64) {
                        shadeContext.fillRect(i, 0, currentStripeWidth, 256);
                    }
                }
                shadeTexture.needsUpdate = true;
            }
        }
    }

    // Animate Fan Blades - tốc độ tăng dần khi bật, giảm dần khi tắt
    if (isFanSpinning && fanSpeed < FAN_MAX_SPEED) {
        fanSpeed = Math.min(fanSpeed + FAN_ACCEL, FAN_MAX_SPEED);
    } else if (!isFanSpinning && fanSpeed > 0) {
        fanSpeed = Math.max(fanSpeed - FAN_DECEL, 0);
    }

    // ĐIỀU KHIỂN ÂM LƯỢNG AUDIO
    if (fanAudio && fanAudio.hasPlaybackControl) {
        // Tốc độ bằng 0 thì volume = 0, đạt max tốc độ thì volume = 0.8 (80%)
        // Chỉ phát tiếng quạt khi 3D đã tải xong
        const volumeTarget = (window.is3DReady === true) ? (fanSpeed / FAN_MAX_SPEED) * 1.2 : 0; 
        fanAudio.setVolume(volumeTarget);
    }
    
    if (fanSpeed > 0 && fanBlades.length > 0) {
        fanBlades.forEach(blade => {
            if (blade) blade.rotation.z -= fanSpeed;
        });
    }

    // --- HIỆU ỨNG VẬT LÝ CHO VÒI PHUN SƯƠNG ---
    if (spraySystem && (spraySystem.visible || (typeof spraySystem2 !== 'undefined' && spraySystem2.visible))) {
        const positions = sprayGeo.attributes.position.array;
        const velocities = sprayGeo.attributes.velocity.array;
        let activeParticles = 0; // Đếm số lượng hạt còn đang lơ lửng trên không
        
        for(let i=0; i < positions.length / 3; i++) {
            // SỬA: Do scale của vòi là 30, nên độ cao 180 đơn vị sẽ tương ứng Y = -6 trong Local Space
            if (positions[i*3+1] > -8 || isSprinklerOn) {
                
                // Trục vớt nếu vừa bật bơm mà hạt đang chìm
                if (positions[i*3+1] < -8 && isSprinklerOn) {
                    positions[i*3+1] = -7; 
                }

                velocities[i*3+1] -= 0.005; // Giảm trọng lực để sương lơ lửng lâu hơn
                
                positions[i*3] += velocities[i*3];
                positions[i*3+1] += velocities[i*3+1];
                positions[i*3+2] += velocities[i*3+2];
                
                // Khi giọt nước chạm đất (Y < -6 tương đương chạm mặt đất thật)
                if (positions[i*3+1] < -6) {
                    if (isSprinklerOn) {
                        // NẾU BƠM ĐANG BẬT: Hút nước lên đầu vòi để xịt tiếp vòng lặp mới
                        positions[i*3] = 0;
                        positions[i*3+1] = 0;
                        positions[i*3+2] = 0;
                        
                        let theta = Math.random() * Math.PI * 2;
                        let phi = Math.random() * Math.PI / 2.2; // Góc tỏa 
                        let speed = 0.05 + Math.random() * 0.15; // Tốc độ văng giảm đi
                        
                        velocities[i*3] = Math.sin(phi) * Math.cos(theta) * speed;
                        velocities[i*3+1] = -(Math.cos(phi) * speed); 
                        velocities[i*3+2] = Math.sin(phi) * Math.sin(theta) * speed;
                        activeParticles++;
                    } else {
                        // NẾU BƠM ĐÃ TẮT: Giấu hẳn hạt này đi xuống lòng đất sâu (không tái tạo nữa)
                        positions[i*3+1] = -9999;
                    }
                } else {
                    // Hạt vẫn đang rơi chưa chạm đất
                    activeParticles++;
                }
            }
        }
        
        sprayGeo.attributes.position.needsUpdate = true;
        
        // NẾU ĐÃ TẮT BƠM VÀ TOÀN BỘ HẠT ĐÃ RƠI CHẠM ĐẤT -> Ẩn hệ thống để giảm giật lag
        if (!isSprinklerOn && activeParticles === 0) {
            spraySystem.visible = false;
            if (typeof spraySystem2 !== 'undefined' && spraySystem2) spraySystem2.visible = false;
        }
    }

    controls.update();
    renderer.render(scene, camera);
}

// Hàm này sẽ được app.js gọi khi có dữ liệu mới từ SSE / MQTT
window.updateTwin = function(data) {
    if (!scene || !greenhouseModel) return;
    
    // Logic cập nhật dựa trên dữ liệu thật (Ví dụ: Quay quạt)
};

// Cập nhật tức thời 3D khi người dùng tương tác nút bấm (tránh độ trễ Wokwi vòng lặp)
window.update3DTwinManual = function(device, value) {
    if (!scene || !greenhouseModel) return;

    if (device === "fan") {
        if (value === true) {
            isFanSpinning = true;
        } else if (value === false) {
            isFanSpinning = false;
        }
        // cập nhật UI text badge tức thời
        const elFan = document.getElementById('sim-live-fan');
        if (elFan) {
            elFan.innerHTML = (value === true) ? "ON" : ((value === false) ? "OFF" : "--");
            elFan.className = "actuator-badge " + ((value === true) ? "on" : "off");
        }
    }
    else if (device === "pump") {
        if (value === true) {
            isSprinklerOn = true;
            if (spraySystem) spraySystem.visible = true;
            if (spraySystem2) spraySystem2.visible = true;
            if (pumpAudio && audioListener && audioListener.context.state === 'running' && window.is3DReady === true) {
                pumpAudio.setVolume(4.0);
            }
        } else if (value === false) {
            isSprinklerOn = false;
            if (pumpAudio) pumpAudio.setVolume(0);
        }
        // cập nhật UI text badge tức thời
        const elPump = document.getElementById('sim-live-pump');
        if (elPump) {
            elPump.innerHTML = (value === true) ? "ON" : ((value === false) ? "OFF" : "--");
            elPump.className = "actuator-badge " + ((value === true) ? "on" : "off");
        }
    }
    else if (device === "servo") {
        if (value === 0) {
            targetStripeWidth = 0;
        } else if (value === 45) {
            targetStripeWidth = 32;
        } else if (value === 90) {
            targetStripeWidth = 64;
        }
        // cập nhật UI text badge tức thời
        const elRoof = document.getElementById('sim-live-roof');
        if (elRoof) {
            elRoof.innerHTML = (value !== null) ? (value + "&deg;") : "--";
        elRoof.className = "actuator-badge " + ((value !== null && value > 0) ? "on" : "off");
        }
    }
    else if (device === "light") {
        // cập nhật UI text badge tức thời
        const elLight = document.getElementById('sim-live-light');
        if (elLight) {
            elLight.innerHTML = (value === true) ? "ON" : ((value === false) ? "OFF" : "--");
            elLight.className = "actuator-badge " + ((value === true) ? "on" : "off");
        }
        if (typeof scifiRectLight !== 'undefined' && scifiRectLight) {
            scifiRectLight.intensity = (value === true) ? 5.0 : 0;
        }
        if (typeof scifiPointLight !== 'undefined' && scifiPointLight) {
            scifiPointLight.intensity = (value === true) ? 10.0 : 0;
        }
        if (typeof scifiLightMaterials !== 'undefined' && Array.isArray(scifiLightMaterials)) {
            scifiLightMaterials.forEach(mat => {
                mat.emissive = new THREE.Color((value === true) ? 0xffcc66 : 0x000000);
                mat.emissiveIntensity = (value === true) ? 2.0 : 0;
            });
        }
    }
    else if (device === "security") {
        securityModeEnabled = (value === true);
        const elSecurity = document.getElementById('sim-live-security');
        if (elSecurity) {
            elSecurity.innerHTML = (value === true) ? "BẬT" : "TẮT";
            elSecurity.className = "actuator-badge " + ((value === true) ? "on" : "off");
        }
    }
};
