# 诊间手语辅助界面更新

## 更新内容

### 1. 识别结果支持删除功能
- **位置**: 诊间手语辅助界面（handsign_assist.html）的识别词汇显示区域
- **功能**: 鼠标悬停在识别出的词汇上时，右上角会显示红色的 × 按钮
- **操作**: 点击词汇或 × 按钮即可删除该词汇
- **实现细节**:
  - 使用 Tailwind CSS 的 `group` 和 `group-hover` 实现悬停效果
  - × 按钮默认透明度为 0，悬停时变为不透明
  - 添加了 `cursor-pointer` 使鼠标悬停时显示手型光标
  - 新增 `removeWord(index)` 方法用于删除指定索引的词汇

### 2. 摄像头分辨率统一
- **配置**: 诊间和挂号界面均使用 640x480 分辨率
- **位置**:
  - 挂号界面: `frontend/handsign.html` (line 1264)
  - 诊间界面: `frontend/js/services/signLanguageAgent.js` (lines 349-350)
- **验证**: 两个界面的摄像头配置已确认一致

## 技术实现

### 删除功能代码片段

```javascript
// 新增删除单个词汇的方法
const removeWord = (index) => {
    continuousWords.value.splice(index, 1);
};
```

```html
<!-- 词汇标签模板 -->
<span v-for="(word, idx) in continuousWords" :key="idx"
      class="bg-green-500 text-white px-3 py-1.5 rounded-full text-sm font-medium shadow-sm animate-fade-in relative group cursor-pointer"
      @click="removeWord(idx)">
    {{ word }}
    <span class="absolute -top-1 -right-1 w-4 h-4 bg-red-500 rounded-full flex items-center justify-center text-xs opacity-0 group-hover:opacity-100 transition-opacity">×</span>
</span>
```

## 用户体验改进

1. **更灵活的编辑**: 用户可以在发送前删除识别错误的词汇，无需清空全部重新识别
2. **视觉反馈**: 悬停时显示删除按钮，提供清晰的交互提示
3. **统一体验**: 诊间和挂号界面使用相同的摄像头分辨率，确保识别效果一致

## 测试建议

1. 打开诊间手语辅助界面
2. 开启连续识别并比划手语
3. 识别出多个词汇后，将鼠标悬停在任意词汇上
4. 确认右上角出现红色 × 按钮
5. 点击词汇或 × 按钮，确认该词汇被删除
6. 验证删除后剩余词汇的顺序和内容正确
