(function () {
    var input = document.querySelector('#ta-dropzone input[type="file"]');
    var previewImg = document.getElementById('ta-dropzone-preview-img');
    var placeholder = document.getElementById('ta-dropzone-placeholder');
    var filename = document.getElementById('ta-dropzone-filename');
    if (!input) return;

    input.addEventListener('change', function () {
        var file = input.files && input.files[0];
        if (!file) return;
        var reader = new FileReader();
        reader.onload = function (e) {
            previewImg.src = e.target.result;
            previewImg.hidden = false;
            placeholder.hidden = true;
            filename.textContent = file.name;
            filename.hidden = false;
        };
        reader.readAsDataURL(file);
    });
})();
