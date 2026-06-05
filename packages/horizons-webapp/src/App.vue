<script setup lang="ts">
import { RouterView } from 'vue-router'
import SupportViewBanner from '@/components/admin/SupportViewBanner.vue'
import { useSupportViewTitle } from '@/composables/useSupportViewTitle'

useSupportViewTitle()
</script>

<template>
  <SupportViewBanner />
  <RouterView />
</template>
