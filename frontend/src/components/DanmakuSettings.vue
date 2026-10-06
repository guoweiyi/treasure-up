<script setup lang="ts">
import { computed } from 'vue';
import PlayerChoice from '../player/PlayerChoice.vue';
import {
  fontOptions,
  fontFamily,
  textShadow,
  type DanmakuPreferences,
} from '../player/preferences';
const prefs = defineModel<DanmakuPreferences>({ required: true });
defineProps<{ count: number | null }>();
const preview = computed(() => ({
  fontFamily: fontFamily(prefs.value),
  fontSize: `${Math.min(prefs.value.fontSize, 40)}px`,
  fontWeight: prefs.value.weight,
  letterSpacing: `${prefs.value.spacing}px`,
  textShadow: textShadow(prefs.value),
  color: prefs.value.uniform ? prefs.value.color : '#fff',
  opacity: prefs.value.opacity / 100,
}));
</script>
<template>
  <div class="player-settings-fields">
    <label class="player-switch"
      ><span
        >显示弹幕 <small v-if="count !== null">{{ count }} 条已保存</small></span
      ><input v-model="prefs.visible" type="checkbox"
    /></label>
    <div class="player-setting-grid">
      <label
        >不透明度 <output>{{ prefs.opacity }}%</output
        ><input v-model.number="prefs.opacity" type="range" aria-label="不透明度" min="0" max="100"
      /></label>
      <label
        >字号 <output>{{ prefs.fontSize }} px</output
        ><input v-model.number="prefs.fontSize" type="range" aria-label="字号" min="12" max="120"
      /></label>
      <label title="限制弹幕占用的行数；底部弹幕仍然靠近画面下沿。"
        >显示区域 <output>{{ prefs.area }}%</output
        ><input
          v-model.number="prefs.area"
          type="range"
          aria-label="显示区域"
          min="25"
          max="100"
          step="5"
      /></label>
      <label title="按固定比例抽样，仅影响本机显示，不修改归档弹幕。"
        >弹幕密度 <output>{{ prefs.density }}%</output
        ><input
          v-model.number="prefs.density"
          type="range"
          aria-label="弹幕密度"
          min="10"
          max="100"
          step="10"
      /></label>
      <label
        >时间偏移（秒）<input
          v-model.number="prefs.offset"
          type="number"
          min="-60"
          max="60"
          step="0.5"
        /><small>正数推迟，负数提前</small></label
      >
    </div>
    <PlayerChoice
      label="弹幕移动速度"
      :model-value="prefs.speed"
      @update:model-value="prefs.speed = Number($event)"
      :choices="[
        { value: 10, label: '很慢' },
        { value: 7.5, label: '较慢' },
        { value: 5, label: '适中' },
        { value: 2.5, label: '较快' },
        { value: 1, label: '很快' },
      ]"
    />
    <p class="player-setting-help">底部弹幕始终避开播放控件；开启防挡字幕会额外留出字幕空间。</p>
    <fieldset class="player-setting-checks">
      <legend>弹幕类型</legend>
      <label><input v-model="prefs.rolling" type="checkbox" />滚动</label
      ><label><input v-model="prefs.top" type="checkbox" />顶部</label
      ><label><input v-model="prefs.bottom" type="checkbox" />底部</label
      ><label><input v-model="prefs.colored" type="checkbox" />彩色弹幕</label>
    </fieldset>
    <fieldset class="player-setting-checks">
      <legend>显示方式</legend>
      <label><input v-model="prefs.antiOverlap" type="checkbox" />防重叠</label
      ><label><input v-model="prefs.subtitleSafe" type="checkbox" />防挡字幕</label
      ><label><input v-model="prefs.scaleWithScreen" type="checkbox" />字号随屏幕缩放</label
      ><label><input v-model="prefs.synchronousPlayback" type="checkbox" />速度同步倍速</label>
    </fieldset>
    <details class="player-settings-details">
      <summary>字体与颜色</summary>
      <div class="player-setting-grid">
        <PlayerChoice
          label="字体"
          :model-value="prefs.fontFamily"
          :choices="fontOptions.map(([value, label]) => ({ value, label }))"
          @update:model-value="prefs.fontFamily = String($event)"
        />
        <label v-if="prefs.fontFamily === 'custom'"
          >本机字体名称<input
            v-model="prefs.customFont"
            maxlength="100"
            placeholder="如 Noto Sans SC"
          /><small>未安装时使用系统替代字体</small></label
        >
        <PlayerChoice
          label="字重"
          :model-value="prefs.weight"
          :choices="[
            { value: 400, label: '常规' },
            { value: 500, label: '中等' },
            { value: 600, label: '半粗' },
            { value: 700, label: '粗体' },
          ]"
          @update:model-value="prefs.weight = Number($event)"
        />
        <PlayerChoice
          label="文字效果"
          :model-value="prefs.outline"
          :choices="[
            { value: 'stroke', label: '描边' },
            { value: 'heavy', label: '重墨' },
            { value: 'shadow', label: '投影' },
            { value: 'none', label: '无' },
          ]"
          @update:model-value="prefs.outline = String($event)"
        />
        <label
          >效果宽度 <output>{{ prefs.strokeWidth }} px</output
          ><input
            v-model.number="prefs.strokeWidth"
            type="range"
            aria-label="效果宽度"
            min="0"
            max="3"
            step="0.5"
        /></label>
        <label
          >字间距 <output>{{ prefs.spacing }} px</output
          ><input
            v-model.number="prefs.spacing"
            type="range"
            aria-label="字间距"
            min="0"
            max="6"
            step="0.5"
        /></label>
        <label>描边颜色<input v-model="prefs.strokeColor" type="color" /></label>
      </div>
      <label class="player-switch"
        ><span>统一文字颜色</span><input v-model="prefs.uniform" type="checkbox" /><input
          v-model="prefs.color"
          aria-label="统一弹幕颜色"
          type="color"
          :disabled="!prefs.uniform"
      /></label>
      <div class="player-danmaku-preview" aria-label="弹幕字体预览">
        <span :style="preview">弹幕显示预览</span>
      </div>
    </details>
  </div>
</template>
